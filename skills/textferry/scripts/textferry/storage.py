# SPDX-License-Identifier: AGPL-3.0-only
"""textferry本地任务目录的JSON状态、产物路径与文件路径管理能力。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .types import JobStatus, PageMode, PageStatus, ScriptTarget


UTC = timezone.utc


class StorageError(RuntimeError):
    """Local storage read/write failure."""


@dataclass(frozen=True)
class JobCreateInput:
    job_id: str
    page_mode: PageMode
    script_target: ScriptTarget
    model_option: str
    total_pages: int
    body_start_hint: Optional[int]
    input_pdf_path: str


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class LocalTextFerryStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def input_pdf_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "input.pdf"

    def job_json_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def page_dir(self, job_id: str, page_no: int) -> Path:
        return self.job_dir(job_id) / "pages" / f"page_{page_no:04d}"

    def page_json_path(self, job_id: str, page_no: int) -> Path:
        return self.page_dir(job_id, page_no) / "page.json"

    def page_raw_path(self, job_id: str, page_no: int) -> Path:
        return self.page_dir(job_id, page_no) / "raw.txt"

    def page_normalized_path(self, job_id: str, page_no: int) -> Path:
        return self.page_dir(job_id, page_no) / "normalized.txt"

    def page_image_path(self, job_id: str, page_no: int) -> Path:
        return self.page_dir(job_id, page_no) / "image.png"

    def batch_pdf_path(self, job_id: str, batch_no: int) -> Path:
        return self.job_dir(job_id) / "batches" / f"batch_{batch_no:04d}.pdf"

    def outputs_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "outputs"

    def artifact_output_path(self, job_id: str, kind: str) -> Path:
        mapping = {
            "best": "best.docx",
            "partial": "partial.docx",
            "final": "final.docx",
            "machineMd": "machine.md",
            "readerDocx": "reader.docx",
            "readerPagedDocx": "reader-paged.docx",
            "readerFlowDocx": "reader-flow.docx",
        }
        filename = mapping.get(kind, f"{kind}.docx")
        return self.outputs_dir(job_id) / filename

    def events_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "events.ndjson"

    def diagnostics_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "diagnostics.ndjson"

    def _relative_to_job(self, job_id: str, path: Optional[Path]) -> Optional[str]:
        if path is None:
            return None
        return path.relative_to(self.job_dir(job_id)).as_posix()

    def _absolute_from_job(self, job_id: str, relative_path: Optional[str]) -> Optional[Path]:
        if not relative_path:
            return None
        return self.job_dir(job_id) / relative_path

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)

    def _read_json(self, path: Path) -> Dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StorageError(f"missing file: {path}") from None
        except json.JSONDecodeError as exc:
            raise StorageError(f"corrupt JSON in {path}: {exc}") from exc

    def create_job(self, payload: JobCreateInput) -> Dict[str, Any]:
        now = to_iso(utc_now())
        job_dir = self.job_dir(payload.job_id)
        (job_dir / "pages").mkdir(parents=True, exist_ok=True)
        self.outputs_dir(payload.job_id).mkdir(parents=True, exist_ok=True)
        (job_dir / "batches").mkdir(parents=True, exist_ok=True)
        self.events_path(payload.job_id).touch()
        self.diagnostics_path(payload.job_id).touch()

        job = {
            "jobId": payload.job_id,
            "status": JobStatus.CREATED.value,
            "pageMode": payload.page_mode.value,
            "scriptTarget": payload.script_target.value,
            "modelOption": payload.model_option,
            "inputPdfPath": self._relative_to_job(payload.job_id, Path(payload.input_pdf_path)),
            "totalPages": payload.total_pages,
            "processedPages": 0,
            "failedPagesCount": 0,
            "bodyStartHint": payload.body_start_hint,
            "bodyStartPdfPage": None,
            "artifacts": {
                "best": None,
                "partial": None,
                "final": None,
                "machineMd": None,
                "readerDocx": None,
                "readerPagedDocx": None,
                "readerFlowDocx": None,
            },
            "alerts": {
                "needsAttention": False,
                "highFailureRate": False,
                "failureRate": 0.0,
                "failureRateThreshold": 0.2,
                "timeoutFailedPages": 0,
                "retryExhaustedPages": 0,
            },
            "diagnostics": {},
            "createdAt": now,
            "updatedAt": now,
        }
        self._write_json(self.job_json_path(payload.job_id), job)

        for page_no in range(1, payload.total_pages + 1):
            page = {
                "pdfPageNo": page_no,
                "status": PageStatus.PENDING.value,
                "retryCount": 0,
                "rawTextPath": None,
                "normalizedTextPath": None,
                "imagePath": None,
                "detectedBookPage": {"raw": None, "normalized": None},
                "resolvedLabel": {
                    "display": f"PDF页:{page_no}",
                    "mode": "pdf",
                    "value": page_no,
                    "fallback": False,
                },
                "errorCode": None,
                "errorMessage": None,
            }
            self._write_json(self.page_json_path(payload.job_id, page_no), page)
        return job

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return self._read_json(self.job_json_path(job_id))

    def save_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job["updatedAt"] = to_iso(utc_now())
        self._write_json(self.job_json_path(str(job["jobId"])), job)
        return job

    def set_job_status(self, job_id: str, status: JobStatus) -> Dict[str, Any]:
        job = self.get_job(job_id)
        job["status"] = status.value
        return self.save_job(job)

    def set_job_body_start_pdf_page(self, job_id: str, pdf_page_start: int) -> Dict[str, Any]:
        job = self.get_job(job_id)
        job["bodyStartPdfPage"] = pdf_page_start
        return self.save_job(job)

    def set_job_artifact(self, job_id: str, kind: str, path: Optional[Path]) -> Dict[str, Any]:
        job = self.get_job(job_id)
        job["artifacts"][kind] = self._relative_to_job(job_id, path)
        return self.save_job(job)

    def update_job_summary(self, job_id: str, *, processed: int, failed: int, alerts: Dict[str, Any], diagnostics: Dict[str, Any]) -> Dict[str, Any]:
        job = self.get_job(job_id)
        job["processedPages"] = processed
        job["failedPagesCount"] = failed
        job["alerts"] = alerts
        job["diagnostics"] = diagnostics
        return self.save_job(job)

    def get_page(self, job_id: str, page_no: int) -> Dict[str, Any]:
        return self._read_json(self.page_json_path(job_id, page_no))

    def save_page(self, job_id: str, page: Dict[str, Any]) -> Dict[str, Any]:
        self._write_json(self.page_json_path(job_id, int(page["pdfPageNo"])), page)
        return page

    def list_pages(self, job_id: str) -> List[Dict[str, Any]]:
        pages_root = self.job_dir(job_id) / "pages"
        result: List[Dict[str, Any]] = []
        for page_json in sorted(pages_root.glob("page_*/page.json")):
            result.append(self._read_json(page_json))
        return result

    def list_pending_pages(self, job_id: str) -> List[Dict[str, Any]]:
        return [
            page
            for page in self.list_pages(job_id)
            if page["status"] in {PageStatus.PENDING.value, PageStatus.PROCESSING.value}
        ]

    def list_failed_pages(self, job_id: str) -> List[Dict[str, Any]]:
        return [page for page in self.list_pages(job_id) if page["status"] == PageStatus.FAILED.value]

    def recover_processing_pages(self, job_id: str) -> int:
        recovered = 0
        for page in self.list_pages(job_id):
            if page["status"] == PageStatus.PROCESSING.value:
                page["status"] = PageStatus.PENDING.value
                self.save_page(job_id, page)
                recovered += 1
        return recovered

    def reset_failed_pages(self, job_id: str) -> int:
        reset = 0
        for page in self.list_pages(job_id):
            if page["status"] != PageStatus.FAILED.value:
                continue
            page["status"] = PageStatus.PENDING.value
            page["errorCode"] = None
            page["errorMessage"] = None
            self.save_page(job_id, page)
            reset += 1
        return reset

    def mark_page_processing(self, job_id: str, page_no: int) -> Dict[str, Any]:
        page = self.get_page(job_id, page_no)
        page["status"] = PageStatus.PROCESSING.value
        page["errorCode"] = None
        page["errorMessage"] = None
        return self.save_page(job_id, page)

    def mark_page_done(
        self,
        job_id: str,
        page_no: int,
        *,
        image_path: Optional[Path],
        raw_text: str,
        normalized_text: str,
        detected_raw: Optional[str],
        detected_norm: Optional[int],
        retry_count: int,
    ) -> Dict[str, Any]:
        page = self.get_page(job_id, page_no)
        raw_path = self.page_raw_path(job_id, page_no)
        normalized_path = self.page_normalized_path(job_id, page_no)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        attempts = sorted((self.page_dir(job_id, page_no) / "ocr-attempts").glob("*.txt"))
        initial_text = attempts[0].read_text(encoding="utf-8") if attempts else raw_text
        raw_path.write_text(initial_text, encoding="utf-8")
        (self.page_dir(job_id, page_no) / "accepted.txt").write_text(raw_text, encoding="utf-8")
        normalized_path.write_text(normalized_text, encoding="utf-8")
        page["status"] = PageStatus.DONE.value
        page["retryCount"] = retry_count
        page["rawTextPath"] = self._relative_to_job(job_id, raw_path)
        page["normalizedTextPath"] = self._relative_to_job(job_id, normalized_path)
        page["imagePath"] = self._relative_to_job(job_id, image_path) if image_path else page["imagePath"]
        page["detectedBookPage"] = {"raw": detected_raw, "normalized": detected_norm}
        page["errorCode"] = None
        page["errorMessage"] = None
        return self.save_page(job_id, page)

    def mark_page_failed(
        self,
        job_id: str,
        page_no: int,
        *,
        error_code: str,
        error_message: str,
        retry_count: int,
        image_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        page = self.get_page(job_id, page_no)
        page["status"] = PageStatus.FAILED.value
        page["retryCount"] = retry_count
        page["errorCode"] = error_code
        page["errorMessage"] = error_message
        if image_path:
            page["imagePath"] = self._relative_to_job(job_id, image_path)
        return self.save_page(job_id, page)

    def set_page_resolved_label(self, job_id: str, page_no: int, resolved_label: Dict[str, Any]) -> Dict[str, Any]:
        page = self.get_page(job_id, page_no)
        page["resolvedLabel"] = resolved_label
        return self.save_page(job_id, page)

    def append_event(
        self,
        job_id: str,
        *,
        event: str,
        page: Optional[int],
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "timestamp": to_iso(utc_now()),
            "event": event,
            "jobId": job_id,
            "page": page,
            "detail": detail or {},
        }
        with self.events_path(job_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append_diagnostic(
        self,
        job_id: str,
        *,
        type_: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "timestamp": to_iso(utc_now()),
            "type": type_,
            "jobId": job_id,
            "message": message,
            "context": context or {},
        }
        with self.diagnostics_path(job_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_events(self, job_id: str) -> List[Dict[str, Any]]:
        path = self.events_path(job_id)
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def read_diagnostics(self, job_id: str) -> List[Dict[str, Any]]:
        path = self.diagnostics_path(job_id)
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def artifact_path(self, job_id: str, kind: str) -> Optional[Path]:
        job = self.get_job(job_id)
        return self._absolute_from_job(job_id, job["artifacts"].get(kind))
