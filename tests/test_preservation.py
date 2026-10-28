import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pymupdf as fitz

from textferry.config import get_textferry_settings
from textferry.service import TextFerryService
from textferry.types import PageMode, ScriptTarget


def test_provider_original_survives_model_denoising(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(pdf)
    get_textferry_settings.cache_clear()
    settings = replace(get_textferry_settings(), data_dir=tmp_path / "data", enable_model_denoise=True)
    service = TextFerryService(settings=settings)
    created = service.create_job_from_pdf_path(pdf_path=pdf, page_mode=PageMode.PDF)
    original, repaired = "首次原始识别，有明显噪声。", "去噪后的可读正文。"
    monkeypatch.setattr("textferry.service.assess_ocr_quality", lambda text, model: SimpleNamespace(accepted=text == repaired, reason="synthetic noise"))
    monkeypatch.setattr(service, "_resolve_denoise_api_key", lambda *args: "test-key")

    async def denoise(**kwargs):
        return repaired

    monkeypatch.setattr(service._sf, "denoise_page_text", denoise)
    accepted, model = asyncio.run(service._accept_or_denoise_raw_text(job_id=created.job_id, page_no=1, raw_text=original, model_key="paddleocr_vl", primary_provider="baidu_async", primary_api_key="test-key"))
    service._mark_page_done_from_raw(job_id=created.job_id, page_no=1, raw_text=accepted, script_target=ScriptTarget.ORIGINAL, retry_count=0, model_key=model, progress=None)
    directory = service.store.page_dir(created.job_id, 1)
    assert (directory / "raw.txt").read_text() == original
    assert (directory / "accepted.txt").read_text() == repaired


def test_conservative_normalization_retains_formulas_links_and_book_titles(tmp_path):
    pdf = tmp_path / "source.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(pdf)
    get_textferry_settings.cache_clear()
    settings = replace(get_textferry_settings(), data_dir=tmp_path / "data")
    service = TextFerryService(settings=settings)
    created = service.create_job_from_pdf_path(pdf_path=pdf, page_mode=PageMode.PDF)
    text = "孟子注疏\n此處為正文。\n公式 $x^{2}$ 與 \\alpha。\nhttps://example.org/source"
    service._mark_page_done_from_raw(job_id=created.job_id, page_no=1, raw_text=text, script_target=ScriptTarget.ORIGINAL, retry_count=0, model_key="deepseek_ocr", progress=None)
    normalized = service.store.page_normalized_path(created.job_id, 1).read_text()
    assert normalized == text
