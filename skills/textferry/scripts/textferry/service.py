# SPDX-License-Identifier: AGPL-3.0-only
"""textferry 的任务创建、执行、恢复、补跑、机器版/人读版导出、按页脚注编号、flow 连续阅读精修、配置化去噪模型、百度降并发、提交队列满/限流暂停与 agent 脚注恢复诊断摘要能力。"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from uuid import uuid4

from .config import (
    is_mock_ocr_enabled,
    ModelRegistry,
    TextFerrySettings,
    get_model_registry,
    get_provider_default_model,
    get_textferry_settings,
    is_model_supported_for_provider,
)
from .docx_export import ExportPage
from .export_cleanup import (
    clean_flow_reader_text,
    clean_reader_text,
    detect_flow_model_polish_signals,
    detect_repeated_noise_lines,
)
from .footnote_detection import (
    DetectedFootnote,
    FootnoteAgentConflict,
    extract_page_footnotes,
    materialize_footnote_text,
    normalize_marker,
)
from .machine_export import write_machine_markdown
from .normalize import normalize_ocr_text
from .ocr_client import BaiduAsyncProvider, TextFerryError, OcrClientError, SiliconFlowProvider
from .page_numbering import detect_body_start, detect_book_page, resolve_labels
from .pdf_render import (
    PdfRenderError,
    export_pdf_pages,
    export_pdf_pages_as_raster_pdf,
    get_pdf_page_count,
    render_pdf_page_png,
)
from .quality import assess_ocr_quality, sanitize_ocr_text
from .reader_export import (
    PreparedReaderFootnote,
    PreparedReaderPage,
    ReaderExportReport,
    ReaderFootnotePageDecision,
    write_reader_docx,
)
from .storage import JobCreateInput, LocalTextFerryStore, from_iso
from .types import JobStatus, PageMode, PageStatus, ScriptTarget


ProgressCallback = Callable[[Dict[str, Any]], None]


@dataclass(frozen=True)
class CreatedJob:
    job_id: str
    status: JobStatus
    total_pages: int
    job_dir: Path


class ProviderUnavailable(RuntimeError):
    """Global provider connectivity failed; leave pages pending for a later run."""


class TextFerryService:
    def __init__(
        self,
        store: Optional[LocalTextFerryStore] = None,
        settings: Optional[TextFerrySettings] = None,
        model_registry: Optional[ModelRegistry] = None,
    ):
        self.settings = settings or get_textferry_settings()
        self.store = store or LocalTextFerryStore(self.settings.data_dir)
        self.model_registry = model_registry or get_model_registry()
        self._sf = SiliconFlowProvider(self.settings)
        self._baidu = BaiduAsyncProvider(self.settings)

    def _resolve_api_key(self, explicit_key: str, provider: str) -> str:
        if is_mock_ocr_enabled():
            return explicit_key.strip() or "mock-api-key"
        if explicit_key.strip():
            return explicit_key.strip()
        env_name = "BAIDU_API_KEY" if provider == "baidu_async" else "SILICONFLOW_API_KEY"
        from .credentials import load_api_key
        local_key = load_api_key(provider)
        if local_key:
            return local_key
        raise ValueError(f"Missing api key: set --api-key or env {env_name}")

    def _emit(self, job_id: str, *, event: str, page: Optional[int] = None, detail: Optional[Dict[str, Any]] = None, progress: Optional[ProgressCallback] = None) -> None:
        self.store.append_event(job_id, event=event, page=page, detail=detail)
        if progress is not None:
            progress({"jobId": job_id, "event": event, "page": page, "detail": detail or {}})

    def _emit_diag(self, job_id: str, *, type_: str, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        self.store.append_diagnostic(job_id, type_=type_, message=message, context=context)

    def _build_job_view(self, job_id: str, job: Dict[str, Any], pages: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        failed_pages = [
            {
                "pdfPageNo": page["pdfPageNo"],
                "retryCount": page["retryCount"],
                "errorCode": page["errorCode"],
                "errorMessage": page["errorMessage"],
            }
            for page in pages
            if page["status"] == PageStatus.FAILED.value
        ]
        total_pages = int(job["totalPages"])
        failed_count = len(failed_pages)
        failure_rate = (failed_count / total_pages) if total_pages else 0.0
        timeout_failed_pages = sum(
            1 for item in failed_pages if self._is_timeout_like_error(str(item.get("errorMessage") or ""))
        )
        retry_exhausted_pages = sum(
            1 for item in failed_pages if int(item.get("retryCount") or 0) >= self.settings.max_retries
        )
        diagnostics = self._collect_job_diagnostics(job_id)
        alerts = {
            "needsAttention": failed_count > 0 or timeout_failed_pages > 0,
            "highFailureRate": failure_rate >= 0.2 if total_pages else False,
            "failureRate": round(failure_rate, 4),
            "failureRateThreshold": 0.2,
            "timeoutFailedPages": timeout_failed_pages,
            "retryExhaustedPages": retry_exhausted_pages,
        }
        processed_pages = sum(1 for page in pages if page["status"] in {PageStatus.DONE.value, PageStatus.FAILED.value})
        return {
            "jobId": job_id,
            "status": job["status"],
            "pageMode": job["pageMode"],
            "scriptTarget": job["scriptTarget"],
            "modelOption": job["modelOption"],
            "progress": {
                "totalPages": total_pages,
                "processedPages": processed_pages,
                "failedPagesCount": failed_count,
            },
            "failedPages": failed_pages,
            "artifacts": job["artifacts"],
            "alerts": alerts,
            "diagnostics": diagnostics,
            "bodyStartHint": job["bodyStartHint"],
            "bodyStartPdfPage": job["bodyStartPdfPage"],
            "createdAt": job["createdAt"],
            "updatedAt": job["updatedAt"],
        }

    def _refresh_job_summary(self, job_id: str) -> Dict[str, Any]:
        job = self.store.get_job(job_id)
        pages = self.store.list_pages(job_id)
        job_view = self._build_job_view(job_id, job, pages)
        self.store.update_job_summary(
            job_id,
            processed=job_view["progress"]["processedPages"],
            failed=job_view["progress"]["failedPagesCount"],
            alerts=job_view["alerts"],
            diagnostics=job_view["diagnostics"],
        )
        refreshed = self.store.get_job(job_id)
        return self._build_job_view(job_id, refreshed, pages)

    def _record_footnote_report(self, job_id: str, *, variant: str, report: ReaderExportReport) -> None:
        self._emit_diag(
            job_id,
            type_="footnote_summary",
            message=f"Collected reader footnote decisions for {variant}",
            context={
                "variant": variant,
                "attemptedPages": report.attempted_pages,
                "materializedNotes": report.materialized_notes,
                "skippedPages": report.skipped_pages,
                "skippedReasons": report.skipped_reasons,
                "markerRecoveredNotes": report.marker_recovered_notes,
                "layoutAssistedNotes": report.layout_assisted_notes,
                "agentResolvedNotes": report.agent_resolved_notes,
                "agentRejectedNotes": report.agent_rejected_notes,
                "agentResolvedPages": report.agent_resolved_pages,
                "agentRejectedPages": report.agent_rejected_pages,
            },
        )
        for decision in report.page_decisions:
            if decision.materialized_notes:
                self._emit_diag(
                    job_id,
                    type_="footnote_materialized",
                    message=f"Materialized {decision.materialized_notes} footnotes on page {decision.page_no}",
                    context={"variant": variant, "page": decision.page_no, "noteCount": decision.materialized_notes},
                )
            if decision.agent_resolved_notes:
                self._emit_diag(
                    job_id,
                    type_="footnote_agent_resolved",
                    message=f"Agent resolved {decision.agent_resolved_notes} ambiguous footnotes on page {decision.page_no}",
                    context={"variant": variant, "page": decision.page_no, "noteCount": decision.agent_resolved_notes},
                )
            if decision.agent_rejected_notes:
                self._emit_diag(
                    job_id,
                    type_="footnote_agent_rejected",
                    message=f"Agent rejected {decision.agent_rejected_notes} ambiguous footnotes on page {decision.page_no}",
                    context={
                        "variant": variant,
                        "page": decision.page_no,
                        "noteCount": decision.agent_rejected_notes,
                        "reason": decision.reason or "agent_rejected",
                    },
                )
            elif decision.reason:
                self._emit_diag(
                    job_id,
                    type_="footnote_skip",
                    message=f"Skipped OCR footnotes on page {decision.page_no}: {decision.reason}",
                    context={"variant": variant, "page": decision.page_no, "reason": decision.reason},
                )

    def _resolve_agent_api_key(self, primary_provider: str, primary_api_key: str) -> str:
        agent_provider = "siliconflow"
        if primary_provider == agent_provider:
            return primary_api_key
        try:
            return self._resolve_api_key("", agent_provider)
        except ValueError:
            return ""

    def _resolve_agent_footnote_model(self):
        configured_key = self.settings.agent_footnote_model_option.strip()
        if configured_key in self.model_registry.options and is_model_supported_for_provider("siliconflow", configured_key):
            return self.model_registry.options[configured_key]
        fallback_key = self._default_model_key_for_provider("siliconflow")
        return self.model_registry.options[fallback_key]

    def _resolve_flow_polish_model(self):
        configured_key = self.settings.flow_polish_model_option.strip()
        if configured_key in self.model_registry.options and is_model_supported_for_provider("siliconflow", configured_key):
            return self.model_registry.options[configured_key]
        fallback_key = self._default_model_key_for_provider("siliconflow")
        return self.model_registry.options[fallback_key]

    def _conflict_context_to_payload(
        self,
        *,
        page: ExportPage,
        conflict: FootnoteAgentConflict,
    ) -> Dict[str, Any]:
        return {
            "pdfPageNo": page.pdf_page_no,
            "resolvedLabel": page.resolved_label,
            "bodyText": conflict.body_text,
            "definitionBlock": conflict.definition_block,
            "anchors": [
                {
                    "rawMarker": anchor.raw_marker,
                    "normalizedMarker": anchor.normalized_marker,
                    "anchorIndex": anchor.index,
                    "bodySnippet": anchor.body_snippet,
                }
                for anchor in conflict.anchors
            ],
            "noteCandidates": [
                {
                    "rawMarker": candidate.raw_marker,
                    "normalizedMarker": candidate.normalized_marker,
                    "text": candidate.text,
                }
                for candidate in conflict.note_candidates
            ],
            "conflictReason": conflict.conflict_reason,
        }

    def _materialize_agent_decision(
        self,
        *,
        decision: Dict[str, Any],
        conflict: FootnoteAgentConflict,
        next_id: int,
    ) -> tuple[List[DetectedFootnote], List[str]]:
        if str(decision.get("status") or "") != "resolved":
            return [], [candidate.display_line() for candidate in conflict.note_candidates]
        if str(decision.get("confidence") or "").lower() != "high":
            return [], [candidate.display_line() for candidate in conflict.note_candidates]

        notes_by_marker = {candidate.normalized_marker: candidate for candidate in conflict.note_candidates}
        anchors_by_index = {anchor.index: anchor for anchor in conflict.anchors}
        created: List[DetectedFootnote] = []
        consumed_markers: set[str] = set()
        current_id = next_id
        matches = decision.get("matches") or []
        if not isinstance(matches, list):
            return [], [candidate.display_line() for candidate in conflict.note_candidates]

        for match in matches:
            if not isinstance(match, dict):
                continue
            normalized_marker = normalize_marker(str(match.get("noteMarker") or ""))
            try:
                anchor_index = int(match.get("anchorIndex"))
            except (TypeError, ValueError):
                continue
            note_candidate = notes_by_marker.get(normalized_marker)
            anchor = anchors_by_index.get(anchor_index)
            if note_candidate is None or anchor is None:
                continue
            if anchor.normalized_marker != normalized_marker or normalized_marker in consumed_markers:
                continue
            placeholder = f"\ue001FN{current_id}\ue002"
            marker_recovered = (
                note_candidate.marker_recovered
                or anchor.marker_recovered
                or note_candidate.raw_marker != anchor.raw_marker
                or note_candidate.normalized_marker != note_candidate.raw_marker
            )
            created.append(
                DetectedFootnote(
                    id=current_id,
                    marker=note_candidate.raw_marker,
                    normalized_marker=note_candidate.normalized_marker,
                    text=note_candidate.text,
                    placeholder=placeholder,
                    anchor_index=anchor.index,
                    marker_recovered=marker_recovered,
                    layout_assisted=conflict.layout_assisted,
                    resolution_source="agent_resolved",
                )
            )
            consumed_markers.add(normalized_marker)
            current_id += 1

        unresolved_lines = [
            candidate.display_line()
            for candidate in conflict.note_candidates
            if candidate.normalized_marker not in consumed_markers
        ]
        return created, unresolved_lines

    async def _prepare_reader_paged_pages(
        self,
        *,
        job_id: str,
        export_pages: Sequence[ExportPage],
        primary_provider: str,
        primary_api_key: str,
    ) -> Dict[int, PreparedReaderPage]:
        repeated_noise_lines = detect_repeated_noise_lines([page.text for page in export_pages if page.text.strip()])
        prepared_pages: Dict[int, PreparedReaderPage] = {}
        next_id = 2
        agent_enabled = self.settings.enable_agent_footnote_resolution
        agent_api_key = self._resolve_agent_api_key(primary_provider, primary_api_key) if agent_enabled else ""
        agent_model = self._resolve_agent_footnote_model() if agent_enabled else None

        for page in export_pages:
            if page.failed:
                continue
            reader_text = clean_reader_text(page.text, repeated_noise_lines)
            detected = extract_page_footnotes(reader_text, next_id=next_id)
            if not detected.attempted:
                continue

            resolved_footnotes = list(detected.resolved_footnotes)
            unresolved_lines = list(detected.unresolved_lines)
            agent_resolved_notes = 0
            agent_rejected_notes = 0
            reason = detected.skipped_reason
            next_id = next_id + len(detected.resolved_footnotes)

            if detected.agent_conflicts:
                for conflict in detected.agent_conflicts:
                    payload = self._conflict_context_to_payload(page=page, conflict=conflict)
                    self._emit_diag(
                        job_id,
                        type_="footnote_conflict",
                        message=f"Escalated ambiguous footnote anchors on page {page.pdf_page_no}",
                        context={
                            "page": page.pdf_page_no,
                            "conflictReason": conflict.conflict_reason,
                            "anchorCount": len(conflict.anchors),
                            "noteCount": len(conflict.note_candidates),
                        },
                    )
                    if not agent_enabled:
                        unresolved_lines.extend(candidate.display_line() for candidate in conflict.note_candidates)
                        agent_rejected_notes += len(conflict.note_candidates)
                        reason = reason or "agent_disabled"
                        continue
                    if not agent_api_key or agent_model is None:
                        unresolved_lines.extend(candidate.display_line() for candidate in conflict.note_candidates)
                        agent_rejected_notes += len(conflict.note_candidates)
                        reason = reason or "agent_unavailable"
                        continue
                    try:
                        decisions = await self._sf.resolve_footnote_conflicts(
                            conflicts=[payload],
                            model_option=agent_model,
                            api_key=agent_api_key,
                        )
                    except OcrClientError as exc:
                        self._emit_provider_error_diag(
                            job_id,
                            exc=exc,
                            provider=exc.provider or "siliconflow",
                            page=page.pdf_page_no,
                            operation="footnote_agent",
                        )
                        unresolved_lines.extend(candidate.display_line() for candidate in conflict.note_candidates)
                        agent_rejected_notes += len(conflict.note_candidates)
                        reason = reason or "agent_error"
                        self._emit_diag(
                            job_id,
                            type_="footnote_agent_rejected",
                            message=f"Agent rejected ambiguous footnotes on page {page.pdf_page_no}: {exc}",
                            context={
                                "page": page.pdf_page_no,
                                "errorType": exc.error_type or type(exc).__name__,
                                "reason": "agent_error",
                            },
                        )
                        continue

                    decision = decisions[0] if decisions else {"status": "rejected", "confidence": "low", "reason": "empty"}
                    created, unresolved = self._materialize_agent_decision(
                        decision=decision,
                        conflict=conflict,
                        next_id=next_id,
                    )
                    if created:
                        resolved_footnotes.extend(created)
                        next_id += len(created)
                        agent_resolved_notes += len(created)
                    else:
                        agent_rejected_notes += len(conflict.note_candidates)
                        self._emit_diag(
                            job_id,
                            type_="footnote_agent_rejected",
                            message=f"Agent rejected ambiguous footnotes on page {page.pdf_page_no}",
                            context={
                                "page": page.pdf_page_no,
                                "confidence": str(decision.get('confidence') or 'low'),
                                "reason": str(decision.get('reason') or 'agent_rejected')[:300],
                            },
                        )
                        reason = reason or "agent_rejected"
                    unresolved_lines.extend(unresolved)

            if detected.skipped_reason and not resolved_footnotes and not detected.agent_conflicts:
                reason = detected.skipped_reason

            prepared_text = materialize_footnote_text(
                detected.body_text,
                resolved_footnotes,
                unresolved_lines=unresolved_lines,
            )
            prepared_pages[page.pdf_page_no] = PreparedReaderPage(
                page_no=page.pdf_page_no,
                text=prepared_text,
                footnotes=[
                    PreparedReaderFootnote(id=footnote.id, placeholder=footnote.placeholder, text=footnote.text)
                    for footnote in resolved_footnotes
                ],
                decision=ReaderFootnotePageDecision(
                    page_no=page.pdf_page_no,
                    attempted=True,
                    materialized_notes=len(resolved_footnotes),
                    agent_resolved_notes=agent_resolved_notes,
                    agent_rejected_notes=agent_rejected_notes,
                    marker_recovered_notes=sum(1 for footnote in resolved_footnotes if footnote.marker_recovered),
                    layout_assisted_notes=sum(1 for footnote in resolved_footnotes if footnote.layout_assisted),
                    reason=reason if not resolved_footnotes else (reason if agent_rejected_notes else None),
                ),
            )
        return prepared_pages

    async def _prepare_reader_flow_pages(
        self,
        *,
        job_id: str,
        export_pages: Sequence[ExportPage],
        prepared_reader_pages: Dict[int, PreparedReaderPage],
        primary_provider: str,
        primary_api_key: str,
    ) -> Dict[int, PreparedReaderPage]:
        repeated_noise_lines = detect_repeated_noise_lines([page.text for page in export_pages if page.text.strip()])
        flow_pages: Dict[int, PreparedReaderPage] = {}
        flow_api_key = (
            self._resolve_agent_api_key(primary_provider, primary_api_key)
            if self.settings.enable_flow_model_polish
            else ""
        )
        flow_model = self._resolve_flow_polish_model() if self.settings.enable_flow_model_polish else None

        for page in export_pages:
            if page.failed:
                continue
            prepared_page = prepared_reader_pages.get(page.pdf_page_no)
            base_text = prepared_page.text if prepared_page is not None else page.text
            flow_text = clean_flow_reader_text(base_text, repeated_noise_lines)
            signals = detect_flow_model_polish_signals(flow_text)
            changed = False

            if (
                signals
                and self.settings.enable_flow_model_polish
                and flow_api_key
                and flow_model is not None
                and "\ue001FN" not in flow_text
            ):
                try:
                    polished_text = await self._sf.polish_flow_text(
                        draft_text=flow_text,
                        model_option=flow_model,
                        api_key=flow_api_key,
                    )
                except OcrClientError as exc:
                    self._emit_provider_error_diag(
                        job_id,
                        exc=exc,
                        provider=exc.provider or "siliconflow",
                        page=page.pdf_page_no,
                        operation="flow_polish",
                    )
                else:
                    polished_clean_text = clean_flow_reader_text(polished_text, set())
                    changed = polished_clean_text != flow_text
                    flow_text = polished_clean_text
                    self._emit_diag(
                        job_id,
                        type_="flow_polish",
                        message=f"Polished suspicious flow text on page {page.pdf_page_no}",
                        context={
                            "page": page.pdf_page_no,
                            "signals": signals,
                            "changed": changed,
                        },
                    )

            flow_pages[page.pdf_page_no] = PreparedReaderPage(
                page_no=page.pdf_page_no,
                text=flow_text,
                footnotes=list(prepared_page.footnotes) if prepared_page is not None else [],
                decision=prepared_page.decision
                if prepared_page is not None
                else ReaderFootnotePageDecision(page_no=page.pdf_page_no, attempted=False, materialized_notes=0),
            )
        return flow_pages

    async def _build_reader_docx_artifacts(
        self,
        job_id: str,
        export_pages: Sequence[ExportPage],
        provider: str,
        api_key: str,
    ) -> None:
        flow_path = self.store.artifact_output_path(job_id, "readerFlowDocx")
        paged_path = self.store.artifact_output_path(job_id, "readerPagedDocx")
        compat_path = self.store.artifact_output_path(job_id, "readerDocx")

        prepared_reader_pages = await self._prepare_reader_paged_pages(
            job_id=job_id,
            export_pages=export_pages,
            primary_provider=provider,
            primary_api_key=api_key,
        )
        prepared_flow_pages = await self._prepare_reader_flow_pages(
            job_id=job_id,
            export_pages=export_pages,
            prepared_reader_pages=prepared_reader_pages,
            primary_provider=provider,
            primary_api_key=api_key,
        )

        write_reader_docx(
            flow_path,
            export_pages,
            preserve_page_breaks=False,
            include_page_headers=False,
            merge_across_pages=True,
            convert_footnotes=True,
            prepared_pages=prepared_flow_pages,
            footnote_restart_mode="continuous",
        )
        paged_report = write_reader_docx(
            paged_path,
            export_pages,
            preserve_page_breaks=True,
            include_page_headers=True,
            convert_footnotes=True,
            prepared_pages=prepared_reader_pages,
            footnote_restart_mode="eachPage",
        )
        shutil.copyfile(flow_path, compat_path)

        self._record_footnote_report(job_id, variant="reader-paged", report=paged_report)
        self.store.set_job_artifact(job_id, "readerDocx", compat_path)
        self.store.set_job_artifact(job_id, "readerFlowDocx", flow_path)
        self.store.set_job_artifact(job_id, "readerPagedDocx", paged_path)

    def _validate_create_inputs(self, pdf_path: Path, model_option: str) -> None:
        if not pdf_path.exists() or not pdf_path.is_file():
            raise ValueError(f"PDF not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError("Only .pdf is supported")
        if pdf_path.stat().st_size > self.settings.max_pdf_size_bytes:
            raise ValueError(f"PDF too large, max {self.settings.max_pdf_size_bytes} bytes")
        if model_option not in self.model_registry.options:
            raise ValueError(f"Unsupported modelOption: {model_option}")

    def _default_model_key_for_provider(self, provider: str) -> str:
        default_key = get_provider_default_model(provider)
        if default_key in self.model_registry.options:
            return default_key
        return self.model_registry.default_option

    def _resolve_runtime_model(self, job_id: str, provider: str, stored_model_key: str) -> Any:
        if is_model_supported_for_provider(provider, stored_model_key):
            return self.model_registry.options[stored_model_key]

        replacement_key = self._default_model_key_for_provider(provider)
        job = self.store.get_job(job_id)
        job["modelOption"] = replacement_key
        job["lastProvider"] = provider
        self.store.save_job(job)
        self._emit_diag(
            job_id,
            type_="provider_warning",
            message=f"Model '{stored_model_key}' is not supported by provider '{provider}', switched to '{replacement_key}'",
            context={
                "provider": provider,
                "requestedModelOption": stored_model_key,
                "effectiveModelOption": replacement_key,
            },
        )
        self._emit(
            job_id,
            event="model_auto_switched",
            detail={
                "provider": provider,
                "requestedModelOption": stored_model_key,
                "effectiveModelOption": replacement_key,
            },
        )
        return self.model_registry.options[replacement_key]

    def _resolve_denoise_api_key(self, primary_provider: str, primary_api_key: str) -> str:
        denoise_provider = self.settings.denoise_provider
        if denoise_provider == primary_provider:
            return primary_api_key
        try:
            return self._resolve_api_key("", denoise_provider)
        except ValueError:
            return ""

    def _resolve_denoise_model(self):
        configured_key = self.settings.denoise_model_option.strip()
        denoise_provider = self.settings.denoise_provider
        if configured_key in self.model_registry.options and is_model_supported_for_provider(denoise_provider, configured_key):
            return self.model_registry.options[configured_key]
        fallback_key = self._default_model_key_for_provider(denoise_provider)
        return self.model_registry.options[fallback_key]

    def _downgrade_baidu_batch_concurrency(self, job_id: str, current: int, reason: str) -> int:
        target = current
        if current > 3:
            target = 3
        elif current > 1:
            target = 1
        if target != current:
            self._emit_diag(
                job_id,
                type_="baidu_concurrency_downgrade",
                message=reason[:300],
                context={"from": current, "to": target},
            )
        return target

    def create_job_from_pdf_path(
        self,
        *,
        pdf_path: Path,
        page_mode: PageMode,
        model_option: Optional[str] = None,
        script_target: ScriptTarget = ScriptTarget.SIMP,
        body_start_hint: Optional[int] = None,
    ) -> CreatedJob:
        resolved_pdf = pdf_path.expanduser().resolve()
        chosen_model = model_option or self._default_model_key_for_provider(self.settings.ocr_provider)
        self._validate_create_inputs(resolved_pdf, chosen_model)
        total_pages = get_pdf_page_count(resolved_pdf)
        if total_pages <= 0:
            raise ValueError("Uploaded PDF has no pages")
        if total_pages > self.settings.max_pdf_pages:
            raise ValueError(f"PDF pages exceed max limit: {self.settings.max_pdf_pages}")

        job_id = uuid4().hex
        job_dir = self.store.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        copied_pdf = self.store.input_pdf_path(job_id)
        shutil.copyfile(resolved_pdf, copied_pdf)

        self.store.create_job(
            JobCreateInput(
                job_id=job_id,
                page_mode=page_mode,
                script_target=script_target,
                model_option=chosen_model,
                total_pages=total_pages,
                body_start_hint=body_start_hint,
                input_pdf_path=str(copied_pdf),
            )
        )
        self._emit(job_id, event="job_created", detail={"pageMode": page_mode.value, "totalPages": total_pages})
        return CreatedJob(job_id=job_id, status=JobStatus.CREATED, total_pages=total_pages, job_dir=job_dir)

    def get_job_view(self, job_id: str) -> Dict[str, Any]:
        job = self.store.get_job(job_id)
        pages = self.store.list_pages(job_id)
        return self._build_job_view(job_id, job, pages)

    def retry_failed(self, job_id: str) -> Dict[str, Any]:
        queued = self.store.reset_failed_pages(job_id)
        if queued:
            self.store.set_job_status(job_id, JobStatus.CREATED)
        self._emit(job_id, event="retry_failed", detail={"retryQueuedPages": queued})
        self._refresh_job_summary(job_id)
        view = self.get_job_view(job_id)
        view["retryQueuedPages"] = queued
        return view

    def confirm_body_start(self, job_id: str, pdf_page_start: int) -> Dict[str, Any]:
        job = self.store.get_job(job_id)
        if job["pageMode"] != PageMode.BODY.value:
            raise ValueError("confirm-body-start is only valid for pageMode=body jobs")
        if job["status"] != JobStatus.WAITING_BODY_START.value:
            raise ValueError("confirm-body-start requires job status WAITING_BODY_START")
        total = int(job["totalPages"])
        if pdf_page_start < 1 or pdf_page_start > total:
            raise ValueError(f"pdfPageStart must be between 1 and {total}")
        self.store.set_job_body_start_pdf_page(job_id, pdf_page_start)
        self.store.set_job_status(job_id, JobStatus.CREATED)
        self._emit(job_id, event="body_start_confirmed", detail={"bodyStartPdfPage": pdf_page_start})
        self._refresh_job_summary(job_id)
        return {"jobId": job_id, "status": JobStatus.CREATED.value, "bodyStartPdfPage": pdf_page_start}

    def get_download_path(self, job_id: str, variant: str) -> Path:
        variant_map = {
            "best": "best",
            "partial": "partial",
            "final": "final",
            "machine-md": "machineMd",
            "reader-docx": "readerDocx",
            "reader-paged-docx": "readerPagedDocx",
            "reader-flow-docx": "readerFlowDocx",
        }
        if variant not in variant_map:
            raise ValueError(
                "variant must be one of best|partial|final|machine-md|reader-docx|reader-paged-docx|reader-flow-docx"
            )
        path = self.store.artifact_path(job_id, variant_map[variant])
        if path and path.exists():
            return path
        raise FileNotFoundError(f"{variant} artifact is not ready")

    def run_job(
        self,
        job_id: str,
        *,
        api_key: str = "",
        provider: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        chosen_provider = (provider or self.settings.ocr_provider).strip().lower()
        resolved_api_key = self._resolve_api_key(api_key, chosen_provider)
        try:
            return asyncio.run(self._run_job_async(job_id, resolved_api_key, chosen_provider, progress))
        except KeyboardInterrupt:
            self.store.recover_processing_pages(job_id)
            self.store.set_job_status(job_id, JobStatus.CREATED)
            self._emit(job_id, event="job_interrupted", detail={"provider": chosen_provider}, progress=progress)
            raise

    async def _run_job_async(
        self,
        job_id: str,
        api_key: str,
        provider: str,
        progress: Optional[ProgressCallback],
    ) -> Dict[str, Any]:
        job = self.store.get_job(job_id)
        if job["status"] == JobStatus.WAITING_BODY_START.value and not job.get("bodyStartPdfPage"):
            return self.get_job_view(job_id)

        self.store.recover_processing_pages(job_id)
        job["lastProvider"] = provider
        model_option = self._resolve_runtime_model(job_id, provider, str(job["modelOption"]))
        job["modelOption"] = model_option.key
        job["status"] = JobStatus.RUNNING.value
        self.store.save_job(job)
        self._emit(job_id, event="job_started", detail={"provider": provider}, progress=progress)

        pending_rows = self.store.list_pending_pages(job_id)
        script_target = ScriptTarget(str(job["scriptTarget"]))

        if pending_rows:
            try:
                await self._process_pending_pages(
                    job_id,
                    pending_rows=pending_rows,
                    model_option=model_option,
                    api_key=api_key,
                    provider=provider,
                    script_target=script_target,
                    progress=progress,
                )
            except ProviderUnavailable as exc:
                self.store.recover_processing_pages(job_id)
                self.store.set_job_status(job_id, JobStatus.CREATED)
                self._emit(job_id, event="job_provider_unavailable", detail={"provider": provider, "error": str(exc)[:300]}, progress=progress)
                return self._refresh_job_summary(job_id)

        pages = self.store.list_pages(job_id)
        page_mode = PageMode(str(job["pageMode"]))
        body_start_pdf_page = job.get("bodyStartPdfPage")
        waiting_for_body_start = False
        if page_mode == PageMode.BODY and not body_start_pdf_page:
            done_rows = [page for page in pages if page["status"] == PageStatus.DONE.value]
            hint = job.get("bodyStartHint")
            body_start_pdf_page = int(hint) if hint else detect_body_start(
                [int(page["pdfPageNo"]) for page in done_rows],
                [
                    (self.store.job_dir(job_id) / page["normalizedTextPath"]).read_text(encoding="utf-8")
                    if page["normalizedTextPath"]
                    else ""
                    for page in done_rows
                ],
            )
            if body_start_pdf_page:
                self.store.set_job_body_start_pdf_page(job_id, int(body_start_pdf_page))
            else:
                waiting_for_body_start = True

        resolved_labels = self._resolve_page_labels(job_id, page_mode, body_start_pdf_page)
        for page_no, label in resolved_labels.items():
            self.store.set_page_resolved_label(job_id, page_no, label)

        export_pages = self._build_export_pages(job_id)
        failed_count = len([page for page in pages if page["status"] == PageStatus.FAILED.value])
        outputs_dir = self.store.outputs_dir(job_id)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        self._write_machine_artifact(job_id, export_pages)

        if self.settings.enable_reader_docx:
            await self._build_reader_docx_artifacts(
                job_id=job_id,
                export_pages=export_pages,
                provider=provider,
                api_key=api_key,
            )
            best_path = self.store.artifact_output_path(job_id, "readerFlowDocx")
        else:
            for key in ("readerDocx", "readerFlowDocx", "readerPagedDocx"):
                self.store.set_job_artifact(job_id, key, None)
            best_path = self.store.artifact_output_path(job_id, "machineMd")

        is_complete = not waiting_for_body_start and failed_count == 0

        if is_complete:
            self.store.set_job_artifact(job_id, "final", best_path)
            self.store.set_job_artifact(job_id, "best", best_path)
            self.store.set_job_artifact(job_id, "partial", None)
            self.store.set_job_status(job_id, JobStatus.FINAL_READY)
            self._emit(job_id, event="job_final_ready", progress=progress)
        elif waiting_for_body_start:
            self.store.set_job_artifact(job_id, "partial", best_path)
            self.store.set_job_artifact(job_id, "best", best_path)
            self.store.set_job_artifact(job_id, "final", None)
            self.store.set_job_status(job_id, JobStatus.WAITING_BODY_START)
            self._emit(job_id, event="job_waiting_body_start", progress=progress)
        else:
            self.store.set_job_artifact(job_id, "partial", best_path)
            self.store.set_job_artifact(job_id, "best", best_path)
            self.store.set_job_artifact(job_id, "final", None)
            self.store.set_job_status(job_id, JobStatus.PARTIAL_READY)
            self._emit(
                job_id,
                event="job_partial_ready",
                detail={"failedPagesCount": failed_count},
                progress=progress,
            )
        return self._refresh_job_summary(job_id)

    async def _process_pending_pages(
        self,
        job_id: str,
        *,
        pending_rows: Sequence[Dict[str, Any]],
        model_option,
        api_key: str,
        provider: str,
        script_target: ScriptTarget,
        progress: Optional[ProgressCallback],
    ) -> None:
        batches: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        effective_batch_size = self.settings.batch_size
        if provider == "baidu_async":
            effective_batch_size = min(self.settings.batch_size, self.settings.baidu_batch_size)
        if provider == "baidu_async":
            import json
            remaining = {row["pdfPageNo"]: row for row in pending_rows}
            for path in sorted((self.store.job_dir(job_id) / "remote-requests").glob("*/batch.json")):
                receipt = path.with_name("receipt.json")
                if not receipt.exists():
                    continue
                state = json.loads(receipt.read_text())
                if state.get("state") == "failed":
                    continue
                batch = json.loads(path.read_text())
                if batch["model"] != model_option.model_id:
                    raise ProviderUnavailable("已有 OCR 回执使用另一模型，请先完成原任务，避免重复提交。")
                if set(batch["pages"]) & set(remaining):
                    batches.append([self.store.get_page(job_id, number) for number in batch["pages"]])
                    for number in batch["pages"]:
                        remaining.pop(number, None)
            pending_rows = list(remaining.values())
        for row in pending_rows:
            current.append(row)
            if len(current) >= effective_batch_size:
                batches.append(current)
                current = []
        if current:
            batches.append(current)

        if provider == "baidu_async":
            effective_concurrency = max(1, self.settings.baidu_batch_concurrency)
            unstable_count = 0

            async def run_batch(batch_no: int, batch_rows: Sequence[Dict[str, Any]], stagger: float = 0.0) -> Dict[str, Any]:
                if stagger > 0:
                    await asyncio.sleep(stagger)
                return await self._process_batch_baidu_async(
                    job_id=job_id,
                    batch_no=batch_no,
                    batch_rows=batch_rows,
                    model_option=model_option,
                    api_key=api_key,
                    script_target=script_target,
                    progress=progress,
                )

            cursor = 0
            while cursor < len(batches):
                wave = batches[cursor : cursor + effective_concurrency]
                batch_offset = cursor + 1
                reports = await asyncio.gather(
                    *(
                        run_batch(batch_offset + index, batch_rows, stagger=index * 0.5)
                        for index, batch_rows in enumerate(wave)
                    )
                )
                wave_unstable_count = sum(1 for report in reports if report.get("providerUnstable"))
                unstable_count += wave_unstable_count
                cursor += len(wave)
                if wave_unstable_count > 0 and cursor < len(batches):
                    effective_concurrency = self._downgrade_baidu_batch_concurrency(
                        job_id,
                        effective_concurrency,
                        f"{wave_unstable_count} batches reported provider instability",
                    )

            if unstable_count > 0:
                self._emit_diag(
                    job_id,
                    type_="baidu_concurrency_note",
                    message=f"{unstable_count} batches reported provider instability",
                    context={"unstableBatches": unstable_count, "totalBatches": len(batches)},
                )
            return

        request_sem = asyncio.Semaphore(max(1, self.settings.max_concurrency))
        for batch in batches:
            await self._process_batch_siliconflow(
                job_id=job_id,
                batch_rows=batch,
                model_option=model_option,
                api_key=api_key,
                script_target=script_target,
                request_sem=request_sem,
                progress=progress,
            )

    def _ensure_page_image(self, job_id: str, page_no: int) -> Path:
        image_path = self.store.page_image_path(job_id, page_no)
        if not image_path.exists():
            render_pdf_page_png(self.store.input_pdf_path(job_id), page_no, image_path, dpi=300)
        return image_path

    def _is_timeout_like_error(self, message: str) -> bool:
        lowered = message.lower()
        return any(marker in lowered for marker in ("timeout", "timed out", "http 504", "gateway timeout"))

    def _is_provider_error_message(self, message: str) -> bool:
        lowered = message.lower()
        return any(marker in lowered for marker in ("network error", " http ", "http ", "baidu ", "siliconflow "))

    def _should_split_baidu_batch(self, error_message: str, row_count: int, exc: Optional[BaseException] = None) -> bool:
        if row_count <= max(1, self.settings.baidu_min_split_pages):
            return False
        if isinstance(exc, OcrClientError) and exc.error_type in {"QueueFull", "RateLimited"}:
            return False
        if isinstance(exc, OcrClientError) and exc.retryable:
            return True
        return self._is_timeout_like_error(error_message) or "network error" in error_message.lower()

    @staticmethod
    def _is_baidu_submit_connect_error(exc: OcrClientError) -> bool:
        return exc.stage == "submit" and exc.error_type == "ConnectError" and exc.retryable is True

    @staticmethod
    def _is_baidu_split_error(message: str) -> bool:
        lowered = message.lower()
        return "拆页" in message or "split" in lowered

    def _emit_provider_error_diag(
        self,
        job_id: str,
        *,
        exc: OcrClientError,
        provider: str,
        page: Optional[int] = None,
        batch_no: Optional[int] = None,
        attempt: Optional[int] = None,
        page_span: Optional[str] = None,
        path: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> None:
        diag_type = "baidu_request_error" if provider == "baidu_async" else "provider_request_error"
        context: Dict[str, Any] = {
            "provider": provider,
            "stage": exc.stage or "unknown",
            "errorType": exc.error_type or type(exc).__name__,
        }
        if exc.provider_code is not None:
            context["providerCode"] = exc.provider_code
        if exc.trace_id:
            context["traceId"] = exc.trace_id
        if page is not None:
            context["page"] = page
        if batch_no is not None:
            context["batchNo"] = batch_no
        if attempt is not None:
            context["attempt"] = attempt
        if page_span is not None:
            context["pageSpan"] = page_span
        if path is not None:
            context["path"] = path
        if operation is not None:
            context["operation"] = operation
        self._emit_diag(job_id, type_=diag_type, message=str(exc)[:300], context=context)

    async def _consume_page_text_map(
        self,
        *,
        job_id: str,
        rows: Sequence[Dict[str, Any]],
        page_text_map: Dict[int, str],
        model_option,
        api_key: str,
        script_target: ScriptTarget,
        attempt: int,
        progress: Optional[ProgressCallback],
    ) -> tuple[List[Dict[str, Any]], Dict[int, str]]:
        failed_rows: List[Dict[str, Any]] = []
        page_errors: Dict[int, str] = {}
        for idx, row in enumerate(rows, start=1):
            page_no = int(row["pdfPageNo"])
            raw_text = str(page_text_map.get(idx) or "").strip()
            if not raw_text:
                failed_rows.append(row)
                page_errors[page_no] = "empty OCR response"
                continue
            try:
                accepted_raw_text, accepted_model_key = await self._accept_or_denoise_raw_text(
                    job_id=job_id,
                    page_no=page_no,
                    raw_text=raw_text,
                    model_key=model_option.key,
                    primary_provider="baidu_async",
                    primary_api_key=api_key,
                )
                self._mark_page_done_from_raw(
                    job_id=job_id,
                    page_no=page_no,
                    raw_text=accepted_raw_text,
                    script_target=script_target,
                    retry_count=int(row["retryCount"]) + attempt - 1,
                    model_key=accepted_model_key,
                    progress=progress,
                )
            except OcrClientError as exc:
                failed_rows.append(row)
                page_errors[page_no] = str(exc)
        return failed_rows, page_errors

    async def _accept_or_denoise_raw_text(
        self,
        *,
        job_id: str,
        page_no: int,
        raw_text: str,
        model_key: str,
        primary_provider: str,
        primary_api_key: str,
    ) -> tuple[str, str]:
        attempts = self.store.page_dir(job_id, page_no) / "ocr-attempts"
        attempts.mkdir(parents=True, exist_ok=True)
        attempt_path = attempts / f"{len(list(attempts.glob('*.txt'))) + 1:04d}.txt"
        attempt_path.write_text(raw_text, encoding="utf-8")
        quality = assess_ocr_quality(raw_text, model_key)
        if quality.accepted:
            return raw_text, model_key

        if not self.settings.enable_model_denoise:
            self._emit_diag(
                job_id,
                type_="quality_gate",
                message=quality.reason,
                context={"page": page_no, "modelKey": model_key},
            )
            raise OcrClientError(f"low quality OCR output: {quality.reason}")

        denoise_api_key = self._resolve_denoise_api_key(primary_provider, primary_api_key)
        if not denoise_api_key:
            self._emit_diag(
                job_id,
                type_="quality_gate",
                message=quality.reason,
                context={"page": page_no, "modelKey": model_key, "denoiseSkipped": True},
            )
            raise OcrClientError(f"low quality OCR output: {quality.reason}")

        denoise_model = self._resolve_denoise_model()
        image_path = self.store.page_image_path(job_id, page_no)
        try:
            repaired_text = await self._sf.denoise_page_text(
                image_path=image_path,
                draft_text=raw_text,
                model_option=denoise_model,
                api_key=denoise_api_key,
            )
        except OcrClientError as exc:
            self._emit_provider_error_diag(
                job_id,
                exc=exc,
                provider=exc.provider or self.settings.denoise_provider,
                page=page_no,
                operation="denoise",
            )
            raise
        repaired_quality = assess_ocr_quality(repaired_text, denoise_model.key)
        if not repaired_quality.accepted:
            self._emit_diag(
                job_id,
                type_="quality_gate",
                message=quality.reason,
                context={
                    "page": page_no,
                    "modelKey": model_key,
                    "denoiseModelKey": denoise_model.key,
                    "denoiseReason": repaired_quality.reason,
                },
            )
            raise OcrClientError(
                f"low quality OCR output: {quality.reason}; denoise failed: {repaired_quality.reason or 'still noisy'}"
            )

        self._emit_diag(
            job_id,
            type_="model_denoise",
            message="Applied page-aware OCR denoise before accepting page text",
            context={
                "page": page_no,
                "primaryProvider": primary_provider,
                "primaryModelKey": model_key,
                "denoiseProvider": self.settings.denoise_provider,
                "denoiseModelKey": denoise_model.key,
                "initialReason": quality.reason,
            },
        )
        self._emit(
            job_id,
            event="page_denoised",
            page=page_no,
            detail={"fromModel": model_key, "toModel": denoise_model.key, "reason": quality.reason},
        )
        return repaired_text, denoise_model.key

    async def _request_baidu_batch(self, *, job_id, page_nos, pdf_path, model_option, api_key):
        """Keep provider receipts and batch identity stable across regrouped resumes."""
        import hashlib
        import shutil
        from .workflow import write_json
        key = hashlib.sha256((model_option.model_id + ":" + ",".join(map(str, page_nos)) + (":raster" if "raster" in pdf_path.stem else "")).encode()).hexdigest()[:32]
        directory = self.store.job_dir(job_id) / "remote-requests" / key
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / ("input_raster.pdf" if "raster" in pdf_path.stem else "input.pdf")
        if not source.exists():
            shutil.copyfile(pdf_path, source)
        write_json(directory / "batch.json", {"pages": page_nos, "model": model_option.model_id})
        receipt = directory / "receipt.json"
        try:
            return await self._baidu.ocr_pdf_pages_baidu_async(pdf_path=source, model_option=model_option, api_key=api_key, expected_pages=len(page_nos), receipt_path=receipt)
        except OcrClientError as exc:
            if receipt.exists() and exc.stage == "submit":
                import json
                saved = json.loads(receipt.read_text())
                if not saved.get("job_id"):
                    if exc.error_type in {"ConnectError", "ConnectTimeout", "PoolTimeout"}:
                        receipt.unlink()
                    else:
                        raise OcrClientError("上次提交是否受理尚不明确，已停止自动重交；请先核对服务端任务。", provider="baidu_async", stage="submit", error_type="SubmissionUnknown", retryable=False) from exc
            raise

    async def _process_batch_baidu_async(
        self,
        *,
        job_id: str,
        batch_no: int,
        batch_rows: Sequence[Dict[str, Any]],
        model_option,
        api_key: str,
        script_target: ScriptTarget,
        progress: Optional[ProgressCallback],
    ) -> Dict[str, Any]:
        max_retries = max(1, self.settings.max_retries)
        remaining_rows = list(batch_rows)
        last_error = "unknown"
        page_errors: Dict[int, str] = {}
        provider_unstable = False
        batch_started = time.perf_counter()
        batch_page_count = len(batch_rows)
        self._emit(job_id, event="batch_started", detail={"batchNo": batch_no, "pageCount": batch_page_count, "provider": "baidu_async"})

        for attempt in range(1, max_retries + 1):
            if not remaining_rows:
                break

            page_nos = [int(row["pdfPageNo"]) for row in remaining_rows]
            page_span = f"{page_nos[0]}-{page_nos[-1]}"
            for row in remaining_rows:
                page_no = int(row["pdfPageNo"])
                self._ensure_page_image(job_id, page_no)
                self.store.mark_page_processing(job_id, page_no)
                self._emit(job_id, event="page_processing", page=page_no, detail={"batchNo": batch_no, "attempt": attempt}, progress=progress)

            batch_pdf = self.store.batch_pdf_path(job_id, batch_no)
            export_pdf_pages(self.store.input_pdf_path(job_id), page_nos, batch_pdf)

            try:
                page_text_map = await self._request_baidu_batch(
                    job_id=job_id, page_nos=page_nos, pdf_path=batch_pdf,
                    model_option=model_option,
                    api_key=api_key,
                )
            except (PdfRenderError, OcrClientError, TextFerryError) as exc:
                last_error = str(exc)
                if isinstance(exc, OcrClientError):
                    provider_unstable = True
                    self._emit_provider_error_diag(
                        job_id,
                        exc=exc,
                        provider="baidu_async",
                        batch_no=batch_no,
                        attempt=attempt,
                        page_span=page_span,
                        path="initial",
                    )
                    if exc.error_type == "SubmissionUnknown" or exc.stage in {"poll", "result"} and exc.error_type != "JobFailed":
                        raise ProviderUnavailable("已保存 OCR 回执；继续任务会查询原任务，不会重新提交。" + str(exc)) from exc
                    if exc.stage == "submit" and exc.error_type in {"QueueFull", "RateLimited"}:
                        # Backpressure is not a bad PDF: preserve pages for a later run.
                        raise ProviderUnavailable(str(exc)) from exc
                    if self._is_baidu_submit_connect_error(exc):
                        if attempt < max_retries:
                            await asyncio.sleep(2 ** (attempt - 1))
                            continue
                        raise ProviderUnavailable(str(exc)) from exc
                if self._is_baidu_split_error(last_error):
                    raster_pdf = batch_pdf.with_name(f"{batch_pdf.stem}_raster.pdf")
                    export_pdf_pages_as_raster_pdf(
                        self.store.input_pdf_path(job_id),
                        page_nos,
                        raster_pdf,
                    )
                    self._emit_diag(
                        job_id,
                        type_="baidu_raster_fallback",
                        message="Retried batch with rasterized PDF after split-page failure",
                        context={
                            "batchNo": batch_no,
                            "attempt": attempt,
                            "pageSpan": page_span,
                            "error": last_error[:300],
                        },
                    )
                    try:
                        page_text_map = await self._request_baidu_batch(
                            job_id=job_id, page_nos=page_nos, pdf_path=raster_pdf,
                            model_option=model_option,
                            api_key=api_key,
                        )
                    except (PdfRenderError, OcrClientError, TextFerryError) as raster_exc:
                        last_error = str(raster_exc)
                        if isinstance(raster_exc, OcrClientError):
                            provider_unstable = True
                            self._emit_provider_error_diag(
                                job_id,
                                exc=raster_exc,
                                provider="baidu_async",
                                batch_no=batch_no,
                                attempt=attempt,
                                page_span=page_span,
                                path="raster_fallback",
                            )
                            if raster_exc.error_type == "SubmissionUnknown" or raster_exc.stage in {"poll", "result"} and raster_exc.error_type != "JobFailed":
                                raise ProviderUnavailable("已保存 OCR 回执，请继续查询原任务。" + str(raster_exc)) from raster_exc
                            if raster_exc.stage == "submit" and raster_exc.error_type in {"QueueFull", "RateLimited"}:
                                raise ProviderUnavailable(str(raster_exc)) from raster_exc
                    else:
                        failed_rows, page_errors = await self._consume_page_text_map(
                            job_id=job_id,
                            rows=remaining_rows,
                            page_text_map=page_text_map,
                            model_option=model_option,
                            api_key=api_key,
                            script_target=script_target,
                            attempt=attempt,
                            progress=progress,
                        )
                        if not failed_rows:
                            duration = round(time.perf_counter() - batch_started, 4)
                            self._emit(
                                job_id,
                                event="batch_finished",
                                detail={"batchNo": batch_no, "pageCount": batch_page_count, "durationSeconds": duration, "provider": "baidu_async"},
                            )
                            return {"providerUnstable": provider_unstable, "durationSeconds": duration}
                        remaining_rows = failed_rows
                        if attempt < max_retries:
                            await asyncio.sleep(2 ** (attempt - 1))
                        continue

                if self._should_split_baidu_batch(last_error, len(remaining_rows), exc if isinstance(exc, OcrClientError) else None):
                    left = list(remaining_rows[: len(remaining_rows) // 2])
                    right = list(remaining_rows[len(remaining_rows) // 2 :])
                    self._emit_diag(
                        job_id,
                        type_="baidu_split",
                        message="Split batch after timeout-like error",
                        context={
                            "batchNo": batch_no,
                            "attempt": attempt,
                            "sourcePages": len(remaining_rows),
                            "pageSpan": page_span,
                            "error": last_error[:300],
                        },
                    )
                    await self._process_batch_baidu_async(
                        job_id=job_id,
                        batch_no=(batch_no * 10) + 1,
                        batch_rows=left,
                        model_option=model_option,
                        api_key=api_key,
                        script_target=script_target,
                        progress=progress,
                    )
                    await self._process_batch_baidu_async(
                        job_id=job_id,
                        batch_no=(batch_no * 10) + 2,
                        batch_rows=right,
                        model_option=model_option,
                        api_key=api_key,
                        script_target=script_target,
                        progress=progress,
                    )
                    duration = round(time.perf_counter() - batch_started, 4)
                    self._emit(
                        job_id,
                        event="batch_finished",
                        detail={"batchNo": batch_no, "pageCount": batch_page_count, "durationSeconds": duration, "provider": "baidu_async"},
                    )
                    return {"providerUnstable": provider_unstable, "durationSeconds": duration}
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
                break

            failed_rows, page_errors = await self._consume_page_text_map(
                job_id=job_id,
                rows=remaining_rows,
                page_text_map=page_text_map,
                model_option=model_option,
                api_key=api_key,
                script_target=script_target,
                attempt=attempt,
                progress=progress,
            )

            if not failed_rows:
                duration = round(time.perf_counter() - batch_started, 4)
                self._emit(
                    job_id,
                    event="batch_finished",
                    detail={"batchNo": batch_no, "pageCount": batch_page_count, "durationSeconds": duration, "provider": "baidu_async"},
                )
                return {"providerUnstable": provider_unstable, "durationSeconds": duration}
            remaining_rows = failed_rows
            if attempt < max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

        for row in remaining_rows:
            page_no = int(row["pdfPageNo"])
            message = page_errors.get(page_no) or last_error
            self._mark_page_failed(job_id, page_no, message, max_retries, progress)
        duration = round(time.perf_counter() - batch_started, 4)
        self._emit(
            job_id,
            event="batch_finished",
            detail={"batchNo": batch_no, "pageCount": batch_page_count, "durationSeconds": duration, "provider": "baidu_async"},
        )
        return {"providerUnstable": provider_unstable, "durationSeconds": duration}

    async def _process_batch_siliconflow(
        self,
        *,
        job_id: str,
        batch_rows: Sequence[Dict[str, Any]],
        model_option,
        api_key: str,
        script_target: ScriptTarget,
        request_sem: asyncio.Semaphore,
        progress: Optional[ProgressCallback],
    ) -> None:
        async def run_row(row: Dict[str, Any]) -> None:
            async with request_sem:
                await self._process_single_page(
                    job_id=job_id,
                    row=row,
                    model_option=model_option,
                    api_key=api_key,
                    script_target=script_target,
                    progress=progress,
                )

        await asyncio.gather(*(run_row(row) for row in batch_rows))


    async def _process_single_page(
        self,
        *,
        job_id: str,
        row: Dict[str, Any],
        model_option,
        api_key: str,
        script_target: ScriptTarget,
        progress: Optional[ProgressCallback],
    ) -> None:
        page_no = int(row["pdfPageNo"])
        max_retries = max(1, self.settings.max_retries)
        last_error = "unknown"
        for attempt in range(1, max_retries + 1):
            try:
                await self._ocr_single_page_once(
                    job_id=job_id,
                    page_no=page_no,
                    model_option=model_option,
                    api_key=api_key,
                    script_target=script_target,
                    retry_count=attempt,
                    progress=progress,
                )
                return
            except (PdfRenderError, OcrClientError, TextFerryError) as exc:
                last_error = str(exc)
                if isinstance(exc, OcrClientError):
                    self._emit_provider_error_diag(
                        job_id,
                        exc=exc,
                        provider=exc.provider or "siliconflow",
                        page=page_no,
                        attempt=attempt,
                        operation="ocr",
                    )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))
        self._mark_page_failed(job_id, page_no, last_error, max_retries, progress)

    async def _ocr_single_page_once(
        self,
        *,
        job_id: str,
        page_no: int,
        model_option,
        api_key: str,
        script_target: ScriptTarget,
        retry_count: int,
        progress: Optional[ProgressCallback],
    ) -> None:
        image_path = self._ensure_page_image(job_id, page_no)
        self.store.mark_page_processing(job_id, page_no)
        self._emit(job_id, event="page_processing", page=page_no, detail={"retryCount": retry_count}, progress=progress)
        raw_text = await self._sf.ocr_image(image_path, model_option, api_key)
        accepted_raw_text, accepted_model_key = await self._accept_or_denoise_raw_text(
            job_id=job_id,
            page_no=page_no,
            raw_text=raw_text,
            model_key=model_option.key,
            primary_provider="siliconflow",
            primary_api_key=api_key,
        )
        self._mark_page_done_from_raw(
            job_id=job_id,
            page_no=page_no,
            raw_text=accepted_raw_text,
            script_target=script_target,
            retry_count=retry_count,
            model_key=accepted_model_key,
            progress=progress,
        )

    def _mark_page_done_from_raw(
        self,
        *,
        job_id: str,
        page_no: int,
        raw_text: str,
        script_target: ScriptTarget,
        retry_count: int,
        model_key: str,
        progress: Optional[ProgressCallback],
    ) -> None:
        quality = assess_ocr_quality(raw_text, model_key)
        if not quality.accepted:
            self._emit_diag(job_id, type_="quality_gate", message=quality.reason, context={"page": page_no, "modelKey": model_key})
            raise OcrClientError(f"low quality OCR output: {quality.reason}")
        cleaned_text = raw_text if self.settings.preserve_source_text else sanitize_ocr_text(raw_text)
        normalized = normalize_ocr_text(cleaned_text, script_target, preserve_content=self.settings.preserve_source_text)
        detection = detect_book_page(cleaned_text)
        self.store.mark_page_done(
            job_id,
            page_no,
            image_path=self.store.page_image_path(job_id, page_no),
            raw_text=raw_text,
            normalized_text=normalized,
            detected_raw=detection.raw,
            detected_norm=detection.normalized,
            retry_count=retry_count,
        )
        self._emit(job_id, event="page_done", page=page_no, detail={"retryCount": retry_count}, progress=progress)

    def _mark_page_failed(
        self,
        job_id: str,
        page_no: int,
        message: str,
        retry_count: int,
        progress: Optional[ProgressCallback],
    ) -> None:
        lowered = message.lower()
        if "denoise failed" in lowered:
            error_code = "OCR_DENOISE_FAILED"
        elif "low quality ocr output" in lowered:
            error_code = "OCR_LOW_QUALITY"
        elif self._is_provider_error_message(message):
            error_code = "OCR_PROVIDER_FAILED"
        else:
            error_code = "OCR_PAGE_FAILED"
        if self._is_timeout_like_error(message):
            self._emit_diag(job_id, type_="timeout", message=message[:300], context={"page": page_no, "retryCount": retry_count})
        self.store.mark_page_failed(
            job_id,
            page_no,
            error_code=error_code,
            error_message=message[:1000],
            retry_count=retry_count,
            image_path=self.store.page_image_path(job_id, page_no),
        )
        self._emit(job_id, event="page_failed", page=page_no, detail={"retryCount": retry_count, "errorCode": error_code, "error": message[:300]}, progress=progress)

    def _collect_job_diagnostics(self, job_id: str) -> Dict[str, Any]:
        diagnostics = self.store.read_diagnostics(job_id)
        events = self.store.read_events(job_id)
        job = self.store.get_job(job_id)
        split_events = [item for item in diagnostics if item.get("type") == "baidu_split"]
        downgrade_events = [item for item in diagnostics if item.get("type") == "baidu_concurrency_downgrade"]
        provider_stage_stats: Dict[str, Dict[str, int]] = {}
        quality_gate_stats = {
            "quality_gate": 0,
            "failedAfterDenoise": 0,
        }
        denoise_stats = {
            "triggered": 0,
        }
        batch_fallback_stats = {
            "rasterFallbacks": 0,
        }
        flow_polish_stats = {
            "triggered": 0,
            "changed": 0,
        }
        footnote_stats = {
            "variant": "reader-paged",
            "attemptedPages": 0,
            "materializedNotes": 0,
            "skippedPages": 0,
            "skippedReasons": {},
            "markerRecoveredNotes": 0,
            "layoutAssistedNotes": 0,
            "agentResolvedNotes": 0,
            "agentRejectedNotes": 0,
            "agentResolvedPages": 0,
            "agentRejectedPages": 0,
        }
        for item in diagnostics:
            diag_type = str(item.get("type") or "")
            context = item.get("context") or {}
            if diag_type in {"baidu_request_error", "provider_request_error"}:
                stage = str(context.get("stage") or "unknown")
                error_type = str(context.get("errorType") or "UnknownError")
                stage_bucket = provider_stage_stats.setdefault(stage, {})
                stage_bucket[error_type] = stage_bucket.get(error_type, 0) + 1
            elif diag_type == "quality_gate":
                quality_gate_stats["quality_gate"] += 1
                if context.get("denoiseReason") or "denoise failed" in str(item.get("message") or "").lower():
                    quality_gate_stats["failedAfterDenoise"] += 1
            elif diag_type == "model_denoise":
                denoise_stats["triggered"] += 1
            elif diag_type == "baidu_raster_fallback":
                batch_fallback_stats["rasterFallbacks"] += 1
            elif diag_type == "flow_polish":
                flow_polish_stats["triggered"] += 1
                if context.get("changed"):
                    flow_polish_stats["changed"] += 1
            elif diag_type == "footnote_summary" and context.get("variant") == "reader-paged":
                footnote_stats["attemptedPages"] = int(context.get("attemptedPages") or 0)
                footnote_stats["materializedNotes"] = int(context.get("materializedNotes") or 0)
                footnote_stats["skippedPages"] = int(context.get("skippedPages") or 0)
                footnote_stats["skippedReasons"] = {
                    str(reason): int(count or 0) for reason, count in (context.get("skippedReasons") or {}).items()
                }
                footnote_stats["markerRecoveredNotes"] = int(context.get("markerRecoveredNotes") or 0)
                footnote_stats["layoutAssistedNotes"] = int(context.get("layoutAssistedNotes") or 0)
                footnote_stats["agentResolvedNotes"] = int(context.get("agentResolvedNotes") or 0)
                footnote_stats["agentRejectedNotes"] = int(context.get("agentRejectedNotes") or 0)
                footnote_stats["agentResolvedPages"] = int(context.get("agentResolvedPages") or 0)
                footnote_stats["agentRejectedPages"] = int(context.get("agentRejectedPages") or 0)
        throughput_stats = self._collect_throughput_stats(job, events, downgrade_events)
        return {
            "provider": job.get("lastProvider") or self.settings.ocr_provider,
            "providerStageStats": provider_stage_stats,
            "qualityGateStats": quality_gate_stats,
            "denoiseStats": denoise_stats,
            "batchFallbackStats": batch_fallback_stats,
            "flowPolishStats": flow_polish_stats,
            "footnoteStats": footnote_stats,
            "throughputStats": throughput_stats,
            "baiduAdaptiveBatching": {
                "configuredBatchSize": self.settings.baidu_batch_size,
                "configuredBatchConcurrency": self.settings.baidu_batch_concurrency,
                "configuredMinSplitPages": self.settings.baidu_min_split_pages,
                "splitEvents": len(split_events),
                "lastSplitAt": split_events[-1]["timestamp"] if split_events else None,
                "lastSplitReason": split_events[-1]["message"] if split_events else None,
                "splitHistory": split_events[-20:],
            },
        }

    def _collect_throughput_stats(
        self,
        job: Dict[str, Any],
        events: Sequence[Dict[str, Any]],
        downgrade_events: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        started_events = [event for event in events if event.get("event") == "job_started"]
        ready_events = [event for event in events if event.get("event") in {"job_final_ready", "job_partial_ready", "job_waiting_body_start"}]
        page_done_events = [event for event in events if event.get("event") == "page_done"]
        batch_finished_events = [event for event in events if event.get("event") == "batch_finished"]

        if started_events:
            start_at = from_iso(str(started_events[0]["timestamp"]))
        else:
            start_at = from_iso(str(job["createdAt"]))
        if ready_events:
            end_at = from_iso(str(ready_events[-1]["timestamp"]))
        else:
            end_at = from_iso(str(job["updatedAt"]))
        total_seconds = max(0.0, (end_at - start_at).total_seconds())
        completed_pages = max(int(job.get("processedPages") or 0), len(page_done_events))
        pages_per_minute = round((completed_pages / total_seconds) * 60, 2) if total_seconds > 0 else 0.0
        batch_durations = [
            float((event.get("detail") or {}).get("durationSeconds") or 0)
            for event in batch_finished_events
            if float((event.get("detail") or {}).get("durationSeconds") or 0) > 0
        ]
        mean_batch_seconds = round(sum(batch_durations) / len(batch_durations), 2) if batch_durations else 0.0
        effective_concurrency = self.settings.baidu_batch_concurrency
        if downgrade_events:
            effective_concurrency = int((downgrade_events[-1].get("context") or {}).get("to") or effective_concurrency)
        return {
            "pagesPerMinute": pages_per_minute,
            "totalRunSeconds": round(total_seconds, 2),
            "meanBatchSeconds": mean_batch_seconds,
            "effectiveBatchConcurrency": effective_concurrency,
            "autoDowngraded": bool(downgrade_events),
        }

    def _resolve_page_labels(self, job_id: str, page_mode: PageMode, body_start_pdf_page: Optional[int]) -> Dict[int, Dict[str, Any]]:
        pages = self.store.list_pages(job_id)
        pdf_pages = [int(page["pdfPageNo"]) for page in pages]
        raw_values = [page["detectedBookPage"]["raw"] for page in pages]
        normalized_values = [page["detectedBookPage"]["normalized"] for page in pages]
        displays = []
        for raw, norm in zip(raw_values, normalized_values):
            if raw is None or norm is None:
                displays.append(None)
            elif str(raw).isdigit():
                displays.append(str(norm))
            elif str(raw).lower() == str(raw) and all(ch in "ivxlcdm" for ch in str(raw).lower()):
                displays.append(f"{raw.lower()}({norm})")
            else:
                displays.append(f"{raw}({norm})")
        resolved = resolve_labels(
            mode=page_mode,
            pdf_page_numbers=pdf_pages,
            raw_values=raw_values,
            normalized_values=normalized_values,
            displays=displays,
            body_start_pdf_page=body_start_pdf_page,
        )
        return {
            item.pdf_page_no: {
                "display": item.display,
                "mode": item.mode.value,
                "value": item.value,
                "fallback": item.fallback,
            }
            for item in resolved
        }

    def _build_export_pages(self, job_id: str) -> List[ExportPage]:
        export_pages: List[ExportPage] = []
        for page in self.store.list_pages(job_id):
            label = page["resolvedLabel"]["display"]
            image_path = self.store.page_image_path(job_id, int(page["pdfPageNo"]))
            normalized_text = ""
            if page["normalizedTextPath"]:
                normalized_text = (self.store.job_dir(job_id) / page["normalizedTextPath"]).read_text(encoding="utf-8")
            export_pages.append(
                ExportPage(
                    pdf_page_no=int(page["pdfPageNo"]),
                    resolved_label=label,
                    text=normalized_text,
                    failed=page["status"] != PageStatus.DONE.value,
                    image_path=image_path if image_path.exists() else None,
                    failure_reason=page["errorMessage"],
                )
            )
        return export_pages

    def _write_machine_artifact(self, job_id: str, export_pages: Sequence[ExportPage]) -> None:
        machine_path = self.store.artifact_output_path(job_id, "machineMd")
        write_machine_markdown(machine_path, export_pages, preserve_content=self.settings.preserve_source_text)
        self.store.set_job_artifact(job_id, "machineMd", machine_path)


@lru_cache(maxsize=1)
def get_textferry_service() -> TextFerryService:
    return TextFerryService()
