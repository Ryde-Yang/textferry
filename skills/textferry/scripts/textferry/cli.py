# SPDX-License-Identifier: AGPL-3.0-only
"""可安装命令 `textferry` 及 create/run/get/retry/confirm/download 子命令 JSON 输出。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Sequence

from .service import get_textferry_service
from .types import PageMode, ScriptTarget


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _progress_logger(total_pages: int):
    state = {"completed": 0}

    def callback(event: Dict[str, Any]) -> None:
        name = str(event.get("event") or "")
        page = event.get("page")
        detail = event.get("detail") or {}
        if name in {"page_done", "page_failed"}:
            state["completed"] += 1
            status = "DONE" if name == "page_done" else "FAILED"
            if page is not None:
                print(f"[{state['completed']}/{total_pages}] page {page} {status}", file=sys.stderr, flush=True)
            return
        if name == "page_processing" and page is not None:
            retry_count = detail.get("retryCount", detail.get("attempt"))
            retry_suffix = f" retry={retry_count}" if retry_count is not None else ""
            print(f"[{state['completed']}/{total_pages}] page {page} PROCESSING{retry_suffix}", file=sys.stderr, flush=True)
            return
        if name.startswith("job_"):
            print(name, file=sys.stderr, flush=True)

    return callback


def _handle_create_job(args: argparse.Namespace) -> int:
    created = get_textferry_service().create_job_from_pdf_path(
        pdf_path=Path(args.pdf_path),
        page_mode=PageMode(args.page_mode),
        model_option=args.model_option,
        script_target=ScriptTarget(args.script_target),
        body_start_hint=args.body_start_hint,
    )
    _print_json(
        {
            "jobId": created.job_id,
            "status": created.status.value,
            "totalPages": created.total_pages,
            "jobDir": str(created.job_dir),
        }
    )
    return 0


def _handle_run_job(args: argparse.Namespace) -> int:
    service = get_textferry_service()
    before = service.get_job_view(args.job_id)
    progress = _progress_logger(int(before["progress"]["totalPages"]))
    try:
        payload = service.run_job(
            args.job_id,
            api_key=args.api_key,
            provider=args.provider,
            progress=progress,
        )
    except KeyboardInterrupt:
        _print_json(service.get_job_view(args.job_id))
        return 130
    _print_json(payload)
    return 0


def _handle_get_job(args: argparse.Namespace) -> int:
    _print_json(get_textferry_service().get_job_view(args.job_id))
    return 0


def _handle_retry_failed(args: argparse.Namespace) -> int:
    _print_json(get_textferry_service().retry_failed(args.job_id))
    return 0


def _handle_confirm_body_start(args: argparse.Namespace) -> int:
    _print_json(get_textferry_service().confirm_body_start(args.job_id, args.pdf_page_start))
    return 0


def _handle_download(args: argparse.Namespace) -> int:
    service = get_textferry_service()
    source_path = service.get_download_path(args.job_id, args.variant)
    output_path = Path(args.output_path).expanduser().resolve()
    if output_path.exists() and not args.force:
        raise FileExistsError(f"{output_path} already exists; pass --force to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, output_path)
    _print_json(
        {
            "jobId": args.job_id,
            "variant": args.variant,
            "sourcePath": str(source_path),
            "outputPath": str(output_path),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advanced textferry OCR engine commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_job = subparsers.add_parser("create-job", description="Create a local OCR job.")
    create_job.add_argument("--pdf-path", required=True, help="Absolute or relative path to the input PDF.")
    create_job.add_argument("--page-mode", default="pdf", choices=[mode.value for mode in PageMode])
    create_job.add_argument("--script-target", default="simp", choices=[target.value for target in ScriptTarget])
    create_job.add_argument("--model-option", default=None)
    create_job.add_argument("--body-start-hint", type=int, default=None)
    create_job.set_defaults(handler=_handle_create_job)

    run_job = subparsers.add_parser("run-job", description="Run a local OCR job.")
    run_job.add_argument("--job-id", required=True)
    run_job.add_argument("--provider", default=None)
    run_job.add_argument("--api-key", default="")
    run_job.set_defaults(handler=_handle_run_job)

    get_job = subparsers.add_parser("get-job", description="Read a local job summary.")
    get_job.add_argument("--job-id", required=True)
    get_job.set_defaults(handler=_handle_get_job)

    retry_failed = subparsers.add_parser("retry-failed", description="Reset failed pages for retry.")
    retry_failed.add_argument("--job-id", required=True)
    retry_failed.set_defaults(handler=_handle_retry_failed)

    confirm_body_start = subparsers.add_parser("confirm-body-start", description="Confirm body start page.")
    confirm_body_start.add_argument("--job-id", required=True)
    confirm_body_start.add_argument("--pdf-page-start", required=True, type=int)
    confirm_body_start.set_defaults(handler=_handle_confirm_body_start)

    download = subparsers.add_parser("download", description="Copy an artifact to a target path.")
    download.add_argument("--job-id", required=True)
    download.add_argument(
        "--variant",
        required=True,
        choices=[
            "best",
            "partial",
            "final",
            "machine-md",
            "reader-docx",
            "reader-paged-docx",
            "reader-flow-docx",
        ],
    )
    download.add_argument("--output-path", required=True)
    download.add_argument("--force", action="store_true", help="Overwrite output path if it already exists.")
    download.set_defaults(handler=_handle_download)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
