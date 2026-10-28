"""从 Skill 目录导入 textferry，并显式创建测试数据根目录。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "skills" / "textferry" / "scripts"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


@pytest.fixture
def make_pdf():
    import pymupdf as fitz  # type: ignore

    def factory(path: Path, page_count: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = fitz.open()
        for _ in range(page_count):
            document.new_page()
        document.save(path)
        document.close()
        return path

    return factory


@pytest.fixture
def textferry_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data_dir = tmp_path / "textferry-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEXTFERRY_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TEXTFERRY_MOCK_OCR", "1")
    monkeypatch.setenv("TEXTFERRY_OCR_PROVIDER", "baidu_async")
    monkeypatch.setenv("TEXTFERRY_ENABLE_READER_DOCX", "1")
    monkeypatch.setenv("TEXTFERRY_PRESERVE_SOURCE_TEXT", "0")
    monkeypatch.setenv("TEXTFERRY_BATCH_SIZE", "10")
    monkeypatch.setenv("TEXTFERRY_BAIDU_BATCH_CONCURRENCY", "1")
    monkeypatch.setenv("TEXTFERRY_MAX_CONCURRENCY", "6")
    monkeypatch.setenv("TEXTFERRY_MAX_RETRIES", "5")
    monkeypatch.setenv("TEXTFERRY_BAIDU_MIN_SPLIT_PAGES", "5")
    monkeypatch.setenv("BAIDU_API_KEY", "test-baidu-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-siliconflow-key")
    return data_dir
