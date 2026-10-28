import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

RUN = Path(__file__).resolve().parents[1] / "skills/textferry/scripts/run.py"


def invoke(*args, env=None):
    return subprocess.run([sys.executable, str(RUN), *map(str, args)], text=True, capture_output=True, env=env)


@pytest.fixture
def chinese_pdf(tmp_path):
    path = tmp_path / "中文论文.pdf"
    with fitz.open() as doc:
        for number in (1, 2):
            page = doc.new_page()
            page.insert_text((50, 70), f"第{number}节 材料整理示例", fontname="china-s", fontsize=18)
            page.insert_text((50, 120), "此處保留繁體原文。孟子注疏是此段讨论的书名。", fontname="china-t", fontsize=12)
            page.insert_text((50, 155), "研究者须核对正文、引文与脚注的归属。[1]", fontname="china-s", fontsize=12)
            page.insert_text((50, 680), "[1] 这是自制脚注示例，不是正式文献引文。", fontname="china-s", fontsize=9)
            page.insert_text((50, 710), "https://example.org/source $x^{2}$", fontsize=10)
            page.insert_text((280, 790), str(number), fontsize=10)
        doc.save(path)
    return path


def test_quick_conversion_preserves_text_source_and_all_page_slots(chinese_pdf, tmp_path):
    output = tmp_path / "bundle"
    result = invoke("convert", chinese_pdf, "--output", output, "--mode", "quick")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["total_pages"] == 2
    assert report["reviewed_pages"] == []
    assert report["status"] == "machine_ready"
    text = (output / "machine.md").read_text()
    for value in ["此處", "孟子注疏", "https://example.org/source", "$x^{2}$", "PDF页:1", "PDF页:2", "[1]"]:
        assert value in text
    assert (output / report["source_pdf"]).is_file()
    assert report["source_sha256"]


def test_review_does_not_claim_pages_checked_just_because_conversion_succeeded(chinese_pdf, tmp_path):
    output = tmp_path / "bundle"
    result = invoke("convert", chinese_pdf, "--output", output, "--mode", "review")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "awaiting_review"
    assert report["unreviewed_pages"] == [1, 2]
    assert (output / "review/pages/0001.png").exists()
    assert (output / "review/pages/0001.md").exists()


def test_review_records_scope_and_invalidates_changed_page(chinese_pdf, tmp_path):
    output = tmp_path / "bundle"
    assert invoke("convert", chinese_pdf, "--output", output, "--mode", "review").returncode == 0
    checked = invoke("review-page", output, "--page", 1, "--checks", "body,order,notes,structure", "--reviewer", "Codex", "--note", "逐页对照正文、脚注和层次；无待核项。")
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["reviewed_pages"] == [1]
    assert report["unreviewed_pages"] == [2]
    page = output / "review/pages/0001.md"
    page.write_text(page.read_text() + "\n再次修改。\n")
    report = json.loads(invoke("report", output).stdout)
    assert report["reviewed_pages"] == []
    assert report["stale_review_pages"] == [1]


def test_conversion_refuses_existing_output(chinese_pdf, tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "mine.txt").write_text("user content")
    result = invoke("convert", chinese_pdf, "--output", output)
    assert result.returncode != 0
    assert (output / "mine.txt").read_text() == "user content"


def test_missing_ocr_key_returns_guided_setup_and_resumable_job(tmp_path):
    pdf = tmp_path / "scan.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.draw_rect(fitz.Rect(40, 40, 200, 200))
        doc.save(pdf)
    env = {k: v for k, v in os.environ.items() if k not in {"BAIDU_API_KEY", "SILICONFLOW_API_KEY", "PADDLEOCR_ACCESS_TOKEN"}}
    env["TEXTFERRY_CONFIG_DIR"] = str(tmp_path / "config")
    env["TEXTFERRY_MOCK_OCR"] = "0"
    output = tmp_path / "bundle"
    result = invoke("convert", pdf, "--output", output, env=env)
    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "needs_setup"
    assert report["onboarding"]["token_url"] == "https://aistudio.baidu.com/account/accessToken"
    assert report["job_id"]
    assert (output / "manifest.json").exists()
    assert json.loads((output / "report.json").read_text())["status"] == "needs_setup"


def test_setup_saves_token_locally_without_echoing_it(tmp_path):
    env = os.environ.copy()
    env["TEXTFERRY_CONFIG_DIR"] = str(tmp_path / "config")
    secret = tmp_path / "token.txt"
    secret.write_text("synthetic-private-test-token")
    result = invoke("configure", "--key-file", secret, env=env)
    assert result.returncode == 0, result.stderr
    assert "synthetic-private-test-token" not in result.stdout + result.stderr
    credentials = tmp_path / "config/credentials.json"
    assert credentials.exists()
    if os.name == "posix":
        assert credentials.stat().st_mode & 0o777 == 0o600


def test_mixed_pdf_routes_only_scan_pages_to_ocr(chinese_pdf, tmp_path, monkeypatch):
    from textferry import workflow
    from textferry.service import TextFerryService
    from textferry.types import PageStatus

    monkeypatch.setenv('PADDLEOCR_ACCESS_TOKEN', 'synthetic-routing-test-token')

    with fitz.open(chinese_pdf) as doc:
        page = doc.new_page()
        page.draw_rect(fitz.Rect(40, 40, 200, 200))
        doc.save(tmp_path / "mixed.pdf")
    captured = []

    def run(self, job_id, **kwargs):
        rows = self.store.list_pending_pages(job_id)
        captured.extend(row["pdfPageNo"] for row in rows)
        for row in rows:
            self.store.mark_page_done(job_id, row["pdfPageNo"], image_path=None, raw_text="扫描页正文。", normalized_text="扫描页正文。", detected_raw=None, detected_norm=None, retry_count=0)
        return self.get_job_view(job_id)

    from unittest.mock import patch
    with patch.object(TextFerryService, "run_job", run):
        report = workflow.convert(tmp_path / "mixed.pdf", tmp_path / "bundle", mode="quick")
    assert captured == [3]
    assert report["processed_pages"] == 3


def test_text_conversion_updates_engine_status(chinese_pdf, tmp_path):
    output = tmp_path / "bundle"
    result = invoke("convert", chinese_pdf, "--output", output)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    job = json.loads((output / "_work/jobs" / report["job_id"] / "job.json").read_text())
    assert job["status"] == "FINAL_READY"
    assert job["processedPages"] == 2


def test_partly_checked_and_unresolved_pages_remain_visible(chinese_pdf, tmp_path):
    output = tmp_path / "bundle"
    invoke("convert", chinese_pdf, "--output", output, "--mode", "review")
    result = invoke("review-page", output, "--page", 1, "--checks", "body", "--reviewer", "Codex", "--note", "本轮只核正文。")
    report = json.loads(result.stdout)
    assert report["reviewed_pages"] == []
    assert report["partially_checked_pages"] == [1]
    for page in (1, 2):
        result = invoke("review-page", output, "--page", page, "--checks", "body,order,notes,structure", "--reviewer", "Codex", "--note", "已回图；仍有疑字。", "--issue", "疑字待核")
    report = json.loads(result.stdout)
    assert report["status"] == "reviewed_with_issues"
    assert report["unresolved_pages"] == [1, 2]


def test_default_baidu_submission_is_sequential_to_avoid_burst_limits(monkeypatch):
    from textferry.config import get_textferry_settings
    monkeypatch.delenv('TEXTFERRY_BAIDU_BATCH_CONCURRENCY', raising=False)
    monkeypatch.delenv('TEXTFERRY_BAIDU_BATCH_SIZE', raising=False)
    get_textferry_settings.cache_clear()
    settings=get_textferry_settings()
    assert settings.baidu_batch_concurrency==1
    assert settings.baidu_batch_size==10
