# SPDX-License-Identifier: AGPL-3.0-only
"""单页或多页 OCR 文本结果、脚注冲突 agent 决议结果、flow 可疑页精修结果，以及包含百度业务码和 traceId 的结构化 provider 错误、非 JSON 响应错误与百度分阶段请求结果。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from pathlib import Path
from typing import Dict, Optional

import httpx

from .config import get_textferry_settings, is_mock_ocr_enabled
from .types import ModelOption


def _mock_pdf_page_text(page_no: int) -> str:
    return f"这是第 {page_no} 页的模拟 OCR 文本。\n第 {page_no} 页"


class OcrClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        stage: Optional[str] = None,
        error_type: Optional[str] = None,
        retryable: Optional[bool] = None,
        provider_code: Optional[int] = None,
        trace_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.stage = stage
        self.error_type = error_type
        self.retryable = retryable
        self.provider_code = provider_code
        self.trace_id = trace_id


class TextFerryError(RuntimeError):
    """Internal processing failure that should fail the affected unit but not crash the job."""


class SiliconFlowProvider:
    provider_name = "siliconflow"

    def __init__(self, settings=None):
        self.settings = settings or get_textferry_settings()

    async def resolve_footnote_conflicts(
        self,
        *,
        conflicts: list[dict],
        model_option: ModelOption,
        api_key: str,
    ) -> list[dict]:
        if is_mock_ocr_enabled():
            return [
                {"status": "rejected", "matches": [], "confidence": "low", "reason": "mock mode"}
                for _ in conflicts
            ]

        decisions: list[dict] = []
        for conflict in conflicts:
            prompt = _build_footnote_resolution_prompt(conflict)
            response_text = await self._complete_text_prompt(
                prompt=prompt,
                model_option=model_option,
                api_key=api_key,
                stage="footnote_resolve",
            )
            decisions.append(_parse_footnote_resolution_response(response_text))
        return decisions

    async def ocr_image(self, image_path: Path, model_option: ModelOption, api_key: str) -> str:
        if is_mock_ocr_enabled():
            return self._mock_ocr(image_path)

        image_bytes = image_path.read_bytes()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"

        payload = {
            "model": model_option.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": model_option.prompt_template},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 2048,
        }
        if model_option.stop_tokens:
            payload["stop"] = list(model_option.stop_tokens)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            transport = httpx.AsyncHTTPTransport(retries=3)
            async with httpx.AsyncClient(transport=transport, timeout=self.settings.ocr_timeout_seconds) as client:
                response = await client.post(self.settings.ocr_base_url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise _provider_network_error("siliconflow", "request", exc) from exc

        if response.status_code >= 400:
            raise _provider_http_error("siliconflow", "request", response)

        data = _parse_provider_json(response, provider="siliconflow", stage="request", retryable=True)
        text = _extract_content_text(data)
        if not text.strip():
            raise OcrClientError(
                "siliconflow OCR returned empty content",
                provider="siliconflow",
                stage="parse",
                error_type="EmptyResponse",
                retryable=False,
            )
        return text

    async def denoise_page_text(
        self,
        image_path: Path,
        draft_text: str,
        model_option: ModelOption,
        api_key: str,
    ) -> str:
        if is_mock_ocr_enabled():
            return draft_text

        image_bytes = image_path.read_bytes()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"
        prompt = (
            "You are repairing OCR for a scanned Chinese book page. "
            "Use the page image as the source of truth and the draft OCR only as a noisy hint. "
            "Remove borders, decorative lines, repeated garbage tokens, isolated symbols, and duplicated noise. "
            "Keep only readable visible text in natural reading order. "
            "If a page number is clearly visible, keep it. Output only cleaned text.\n\n"
            f"Draft OCR:\n{draft_text[:4000]}"
        )

        payload = {
            "model": model_option.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 2048,
        }
        if model_option.stop_tokens:
            payload["stop"] = list(model_option.stop_tokens)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            transport = httpx.AsyncHTTPTransport(retries=3)
            async with httpx.AsyncClient(transport=transport, timeout=self.settings.ocr_timeout_seconds) as client:
                response = await client.post(self.settings.ocr_base_url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise _provider_network_error("siliconflow", "denoise", exc) from exc

        if response.status_code >= 400:
            raise _provider_http_error("siliconflow", "denoise", response)

        text = _extract_content_text(
            _parse_provider_json(response, provider="siliconflow", stage="denoise", retryable=True)
        )
        if not text.strip():
            raise OcrClientError(
                "siliconflow denoise returned empty content",
                provider="siliconflow",
                stage="parse",
                error_type="EmptyResponse",
                retryable=False,
            )
        return text

    async def polish_flow_text(
        self,
        *,
        draft_text: str,
        model_option: ModelOption,
        api_key: str,
    ) -> str:
        if is_mock_ocr_enabled():
            return draft_text

        prompt = (
            "You are polishing OCR text for a human-readable continuous-reading Chinese book document.\n"
            "Your job is to lightly repair noisy page-level text without changing facts.\n"
            "Rules:\n"
            "1. Remove standalone running headers, running footers, and standalone page-number lines such as Chinese digit page labels.\n"
            "2. Remove embedded page-number artifacts only when they are obvious OCR leftovers inside otherwise normal Chinese prose.\n"
            "3. Merge obviously broken line wraps into natural paragraphs when the sentence clearly continues.\n"
            "4. Preserve all factual wording, quotations, note markers, and scholarly content. Do not summarize or rewrite.\n"
            "5. Output only cleaned text.\n\n"
            f"Draft text:\n{draft_text[:5000]}"
        )
        return await self._complete_text_prompt(
            prompt=prompt,
            model_option=model_option,
            api_key=api_key,
            stage="flow_polish",
        )

    def _mock_ocr(self, image_path: Path) -> str:
        stem = image_path.stem
        digits = "".join(ch for ch in stem if ch.isdigit())
        page_no = int(digits) if digits else 0
        return _mock_pdf_page_text(page_no)

    async def _complete_text_prompt(
        self,
        *,
        prompt: str,
        model_option: ModelOption,
        api_key: str,
        stage: str,
    ) -> str:
        payload = {
            "model": model_option.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 1200,
        }
        if model_option.stop_tokens:
            payload["stop"] = list(model_option.stop_tokens)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            transport = httpx.AsyncHTTPTransport(retries=3)
            async with httpx.AsyncClient(transport=transport, timeout=self.settings.ocr_timeout_seconds) as client:
                response = await client.post(self.settings.ocr_base_url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise _provider_network_error("siliconflow", stage, exc) from exc

        if response.status_code >= 400:
            raise _provider_http_error("siliconflow", stage, response)

        text = _extract_content_text(_parse_provider_json(response, provider="siliconflow", stage=stage, retryable=True))
        if not text.strip():
            raise OcrClientError(
                f"siliconflow {stage} returned empty content",
                provider="siliconflow",
                stage="parse",
                error_type="EmptyResponse",
                retryable=False,
            )
        return text


class BaiduAsyncProvider:
    provider_name = "baidu_async"

    def __init__(self, settings=None):
        self.settings = settings or get_textferry_settings()

    async def ocr_pdf_pages_baidu_async(
        self,
        *,
        pdf_path: Path,
        model_option: ModelOption,
        api_key: str,
        expected_pages: int,
        receipt_path: Optional[Path] = None,
    ) -> Dict[int, str]:
        if expected_pages <= 0:
            return {}
        if is_mock_ocr_enabled():
            return {page_no: _mock_pdf_page_text(page_no) for page_no in range(1, expected_pages + 1)}

        from .workflow import digest, write_json
        from .progress import emit
        receipt_path = Path(receipt_path) if receipt_path else None
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path and receipt_path.exists() else {}
        identity = {"pdf_sha256": digest(pdf_path), "model": model_option.model_id, "expected_pages": expected_pages}
        if receipt and any(receipt.get(key) != value for key, value in identity.items()):
            raise ValueError("OCR 回执与当前批次不一致，已停止，避免提交错误或重复任务。")
        if receipt.get("result") is not None:
            return {int(key): value for key, value in receipt["result"].items()}
        job_id = receipt.get("job_id")
        if receipt and not job_id:
            raise OcrClientError("上次提交结果尚不明确，已停止自动重交；请先在服务端核对该次任务。", provider="baidu_async", stage="submit", error_type="SubmissionUnknown", retryable=False)
        def save_receipt(**values):
            receipt.update(identity)
            receipt.update(values)
            if receipt_path:
                write_json(receipt_path, receipt)
        stage = "poll" if job_id else "submit"
        submit_url = f"{self.settings.baidu_ocr_base_url}/api/v2/ocr/jobs"
        headers = {"Authorization": f"Bearer {api_key}"}
        files = {"file": (pdf_path.name, pdf_path.read_bytes(), "application/pdf")}
        data = {"model": model_option.model_id}

        try:
            transport = httpx.AsyncHTTPTransport(retries=3)
            async with httpx.AsyncClient(transport=transport, timeout=_baidu_timeout(self.settings.baidu_connect_timeout_seconds, self.settings.baidu_submit_read_timeout_seconds)) as client:
                if not job_id:
                    emit("ocr_submit", "正在提交扫描页识别", total=expected_pages)
                    save_receipt(state="submitting")
                    submit_resp = await _baidu_post(
                        client,
                        submit_url,
                        headers=headers,
                        files=files,
                        data=data,
                    )

                    if submit_resp.status_code >= 400:
                        try:
                            error_payload = submit_resp.json()
                        except ValueError:
                            error_payload = {}
                        if not isinstance(error_payload, dict):
                            error_payload = {}
                        code = error_payload.get("code")
                        error_type = "HTTPStatus"
                        if code == 10010:
                            error_type = "QueueFull"
                        elif code == 12002 or submit_resp.status_code == 429:
                            error_type = "RateLimited"
                        if receipt_path and 400 <= submit_resp.status_code < 500 and submit_resp.status_code != 408:
                            receipt_path.unlink(missing_ok=True)
                        raise OcrClientError(
                            f"baidu submit http {submit_resp.status_code}: {_extract_error_detail(submit_resp)}",
                            provider="baidu_async",
                            stage="submit",
                            error_type=error_type,
                            retryable=error_type in {"QueueFull", "RateLimited"} or _http_status_retryable(submit_resp.status_code),
                            provider_code=code if isinstance(code, int) else None,
                            trace_id=error_payload.get("traceId"),
                        )

                    submit_payload = _parse_provider_json(
                        submit_resp,
                        provider="baidu_async",
                        stage="submit",
                        retryable=True,
                    )
                    job_id = str(((submit_payload.get("data") or {}).get("jobId") or "")).strip()
                    if not job_id:
                        raise OcrClientError(
                            f"baidu submit missing jobId: {submit_resp.text[:400]}",
                            provider="baidu_async",
                            stage="submit",
                            error_type="MissingJobId",
                            retryable=False,
                        )

                    save_receipt(job_id=job_id, state="accepted")
                emit("ocr_wait", "识别任务已受理，正在等待返回结果", total=expected_pages)
                stage = "poll"
                status_url = f"{self.settings.baidu_ocr_base_url}/api/v2/ocr/jobs/{job_id}"
                start = time.monotonic()
                last_notice = start
                while True:
                    poll_resp = await _baidu_get(
                        client,
                        status_url,
                        headers=headers,
                        stage="poll",
                        timeout=_baidu_timeout(self.settings.baidu_connect_timeout_seconds, self.settings.baidu_poll_read_timeout_seconds),
                    )
                    if poll_resp.status_code >= 400:
                        raise OcrClientError(
                            f"baidu poll http {poll_resp.status_code}: {_extract_error_detail(poll_resp)}",
                            provider="baidu_async",
                            stage="poll",
                            error_type="HTTPStatus",
                            retryable=_http_status_retryable(poll_resp.status_code),
                            )
                    payload = _parse_provider_json(
                        poll_resp,
                        provider="baidu_async",
                        stage="poll",
                        retryable=True,
                    )
                    now = time.monotonic()
                    if now - last_notice >= 30:
                        emit("ocr_wait", f"仍在等待识别结果，已等待 {int(now - start)} 秒", total=expected_pages)
                        last_notice = now
                    data_obj = payload.get("data") or {}
                    state = str(data_obj.get("state") or "").lower()
                    if state == "done":
                        result_url = (data_obj.get("resultUrl") or {}).get("jsonUrl")
                        if not isinstance(result_url, str) or not result_url.strip():
                            raise OcrClientError(
                                "baidu result missing jsonUrl",
                                provider="baidu_async",
                                stage="poll",
                                error_type="MissingResultUrl",
                                retryable=False,
                            )
                        emit("ocr_download", "识别已完成，正在取回结果", total=expected_pages)
                        result_resp = await _baidu_get(
                            client,
                            result_url,
                            headers=None,
                            stage="result",
                            timeout=_baidu_timeout(self.settings.baidu_connect_timeout_seconds, self.settings.baidu_result_read_timeout_seconds),
                        )
                        if result_resp.status_code >= 400:
                            raise OcrClientError(
                                f"baidu result http {result_resp.status_code}: {_extract_error_detail(result_resp)}",
                                provider="baidu_async",
                                stage="result",
                                error_type="HTTPStatus",
                                retryable=_http_status_retryable(result_resp.status_code),
                            )
                        result = _parse_baidu_jsonl_page_texts(result_resp.text)
                        save_receipt(state="done", result=result)
                        return result

                    if state == "failed":
                        save_receipt(state="failed")
                        raise OcrClientError(
                            str(data_obj.get("errorMsg") or "baidu async OCR failed"),
                            provider="baidu_async",
                            stage="poll",
                            error_type="JobFailed",
                            retryable=False,
                        )

                    if (time.monotonic() - start) > self.settings.baidu_ocr_job_timeout_seconds:
                        raise OcrClientError(
                            "baidu async OCR polling timeout",
                            provider="baidu_async",
                            stage="poll",
                            error_type="Timeout",
                            retryable=True,
                        )

                    await asyncio.sleep(max(0.1, self.settings.baidu_ocr_poll_seconds))
        except httpx.HTTPError as exc:
            detail = str(exc) or repr(exc)
            raise OcrClientError(
                f"baidu {stage} network error [{type(exc).__name__}]: {detail}",
                provider="baidu_async",
                stage=stage,
                error_type=type(exc).__name__,
                retryable=True,
            ) from exc


def _baidu_timeout(connect_timeout_seconds: float, read_timeout_seconds: float) -> httpx.Timeout:
    return httpx.Timeout(
        connect=max(0.1, connect_timeout_seconds),
        read=max(0.1, read_timeout_seconds),
        write=max(0.1, read_timeout_seconds),
        pool=max(0.1, connect_timeout_seconds),
    )


async def _baidu_post(client: httpx.AsyncClient, url: str, *, headers, files, data) -> httpx.Response:
    try:
        return await client.post(url, headers=headers, files=files, data=data)
    except httpx.HTTPError as exc:
        detail = str(exc) or repr(exc)
        raise OcrClientError(
            f"baidu submit network error [{type(exc).__name__}]: {detail}",
            provider="baidu_async",
            stage="submit",
            error_type=type(exc).__name__,
            retryable=True,
        ) from exc


async def _baidu_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers,
    stage: str,
    timeout: httpx.Timeout,
) -> httpx.Response:
    try:
        return await client.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        detail = str(exc) or repr(exc)
        raise OcrClientError(
            f"baidu {stage} network error [{type(exc).__name__}]: {detail}",
            provider="baidu_async",
            stage=stage,
            error_type=type(exc).__name__,
            retryable=True,
        ) from exc


def _provider_network_error(provider: str, stage: str, exc: httpx.HTTPError) -> OcrClientError:
    detail = str(exc) or repr(exc)
    return OcrClientError(
        f"{provider} {stage} network error [{type(exc).__name__}]: {detail}",
        provider=provider,
        stage=stage,
        error_type=type(exc).__name__,
        retryable=True,
    )


def _provider_http_error(provider: str, stage: str, response: httpx.Response) -> OcrClientError:
    status_code = response.status_code
    detail = _extract_error_detail(response)
    return OcrClientError(
        f"{provider} {stage} http {status_code}: {detail}",
        provider=provider,
        stage=stage,
        error_type="HTTPStatus",
        retryable=_http_status_retryable(status_code),
    )


def _parse_provider_json(response: httpx.Response, *, provider: str, stage: str, retryable: bool) -> object:
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        detail = response.text[:300] if response.text else "empty response"
        raise OcrClientError(
            f"{provider} {stage} invalid JSON response: {detail}",
            provider=provider,
            stage=stage,
            error_type="JSONDecodeError",
            retryable=retryable,
        ) from exc


def _http_status_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _parse_baidu_jsonl_page_texts(text: str) -> Dict[int, str]:
    page_map: Dict[int, str] = {}
    lines = [line for line in text.splitlines() if line.strip()]
    page_no = 1

    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        raw_result = obj.get("result")
        if not isinstance(raw_result, dict):
            continue

        layout = raw_result.get("layoutParsingResults")
        if isinstance(layout, list) and layout:
            for item in layout:
                page_text = _extract_layout_page_text(item)
                if page_text:
                    page_map[page_no] = page_text
                page_no += 1
            continue

        markdown = raw_result.get("markdown")
        markdown_text = ""
        if isinstance(markdown, dict):
            text_val = markdown.get("text")
            if isinstance(text_val, str):
                markdown_text = text_val.strip()
        if markdown_text:
            page_map[page_no] = markdown_text
            page_no += 1

    return page_map


def _extract_layout_page_text(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    pruned = item.get("prunedResult")
    if not isinstance(pruned, dict):
        return ""
    blocks = pruned.get("parsing_res_list")
    if not isinstance(blocks, list):
        return ""
    pieces = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        content = block.get("block_content")
        if isinstance(content, str) and content.strip():
            pieces.append(content.strip())
    return "\n".join(pieces).strip()


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            if isinstance(data.get("error"), dict):
                err = data["error"]
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
            detail = data.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            msg = data.get("msg")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
            err_msg = ((data.get("data") or {}).get("errorMsg") if isinstance(data.get("data"), dict) else None)
            if isinstance(err_msg, str) and err_msg.strip():
                return err_msg.strip()
    except Exception:
        pass
    return response.text[:400] if response.text else "unknown error"


def _extract_content_text(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def _build_footnote_resolution_prompt(conflict: dict) -> str:
    serialized = json.dumps(conflict, ensure_ascii=False, indent=2)
    return (
        "You are resolving OCR footnote anchors for one scanned Chinese book page.\n"
        "Choose only when the mapping is clearly supported by the page text.\n"
        "If uncertain, reject.\n"
        "Return JSON only with this schema:\n"
        '{"status":"resolved|rejected","matches":[{"noteMarker":"①","anchorIndex":0}],"confidence":"high|medium|low","reason":"short explanation"}\n'
        "Rules:\n"
        "- Use only anchorIndex values from anchors.\n"
        "- Only map note markers that appear in noteCandidates.\n"
        "- If confidence is not high, set status to rejected or confidence below high.\n"
        "- Do not invent notes or anchors.\n\n"
        f"Conflict:\n{serialized}"
    )


def _parse_footnote_resolution_response(text: str) -> dict:
    payload = _extract_first_json_object(text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OcrClientError(
            "footnote agent returned invalid JSON",
            provider="siliconflow",
            stage="parse",
            error_type="JSONDecodeError",
            retryable=False,
        ) from exc
    if not isinstance(data, dict):
        raise OcrClientError(
            "footnote agent returned non-object JSON",
            provider="siliconflow",
            stage="parse",
            error_type="InvalidJSONShape",
            retryable=False,
        )
    status = str(data.get("status") or "").strip().lower()
    confidence = str(data.get("confidence") or "").strip().lower()
    matches = data.get("matches") or []
    if status not in {"resolved", "rejected"}:
        raise OcrClientError(
            "footnote agent response missing valid status",
            provider="siliconflow",
            stage="parse",
            error_type="InvalidStatus",
            retryable=False,
        )
    if confidence not in {"high", "medium", "low"}:
        raise OcrClientError(
            "footnote agent response missing valid confidence",
            provider="siliconflow",
            stage="parse",
            error_type="InvalidConfidence",
            retryable=False,
        )
    if not isinstance(matches, list):
        raise OcrClientError(
            "footnote agent response matches must be a list",
            provider="siliconflow",
            stage="parse",
            error_type="InvalidMatches",
            retryable=False,
        )
    normalized_matches = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        normalized_matches.append(
            {
                "noteMarker": str(item.get("noteMarker") or ""),
                "anchorIndex": item.get("anchorIndex"),
            }
        )
    return {
        "status": status,
        "matches": normalized_matches,
        "confidence": confidence,
        "reason": str(data.get("reason") or "")[:500],
    }


def _extract_first_json_object(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped
