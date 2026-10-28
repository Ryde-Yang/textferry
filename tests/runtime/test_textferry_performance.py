"""create-job 与纯读 get-job 的基础性能回归测试。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from textferry.config import get_model_registry, get_textferry_settings
from textferry.service import TextFerryService
from textferry.types import PageMode


def build_service() -> TextFerryService:
    get_textferry_settings.cache_clear()
    get_model_registry.cache_clear()
    return TextFerryService()


def test_create_job_and_get_job_performance(textferry_env: Path, make_pdf) -> None:
    pdf_path = make_pdf(textferry_env / "hundred-pages.pdf", 100)
    service = build_service()
    create_budget = float(os.getenv("TEXTFERRY_CREATE_JOB_MAX_SECONDS", "2.0"))
    get_budget = float(os.getenv("TEXTFERRY_GET_JOB_MAX_SECONDS", "0.2"))

    start = time.perf_counter()
    created = service.create_job_from_pdf_path(pdf_path=pdf_path, page_mode=PageMode.PDF)
    create_elapsed = time.perf_counter() - start

    before = service.store.get_job(created.job_id)
    start = time.perf_counter()
    job_view = service.get_job_view(created.job_id)
    get_elapsed = time.perf_counter() - start
    after = service.store.get_job(created.job_id)

    assert job_view["progress"]["totalPages"] == 100
    assert before == after
    assert create_elapsed <= create_budget
    assert get_elapsed <= get_budget
