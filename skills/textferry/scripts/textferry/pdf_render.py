# SPDX-License-Identifier: AGPL-3.0-only
"""页数探测、单页渲染图片与批次 PDF 导出能力。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


class PdfRenderError(RuntimeError):
    pass


def _import_fitz():
    try:
        import pymupdf as fitz  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise PdfRenderError(
            "PyMuPDF is required for TextFerry OCR. Install package 'pymupdf'."
        ) from exc
    return fitz


def get_pdf_page_count(pdf_path: Path) -> int:
    fitz = _import_fitz()
    try:
        with fitz.open(pdf_path) as doc:
            return int(doc.page_count)
    except Exception as exc:
        raise PdfRenderError(f"failed to open PDF: {exc}") from exc


def render_pdf_page_png(pdf_path: Path, page_no: int, output_path: Path, dpi: int = 300) -> Path:
    fitz = _import_fitz()
    try:
        with fitz.open(pdf_path) as doc:
            page = doc.load_page(page_no - 1)
            scale = dpi / 72.0
            matrix = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(output_path))
        return output_path
    except Exception as exc:
        raise PdfRenderError(f"failed to render page {page_no}: {exc}") from exc


def export_pdf_pages(pdf_path: Path, page_numbers: Iterable[int], output_path: Path) -> Path:
    fitz = _import_fitz()
    ordered = sorted({int(page_no) for page_no in page_numbers})
    if not ordered:
        raise PdfRenderError("no pages requested")
    try:
        with fitz.open(pdf_path) as src:
            out = fitz.open()
            for page_no in ordered:
                if page_no < 1 or page_no > int(src.page_count):
                    out.close()
                    raise PdfRenderError(f"invalid page number: {page_no}")
                out.insert_pdf(src, from_page=page_no - 1, to_page=page_no - 1)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            out.save(output_path)
            out.close()
        return output_path
    except PdfRenderError:
        raise
    except Exception as exc:
        raise PdfRenderError(f"failed to export pages {ordered}: {exc}") from exc


def export_pdf_pages_as_raster_pdf(
    pdf_path: Path,
    page_numbers: Iterable[int],
    output_path: Path,
    dpi: int = 200,
) -> Path:
    fitz = _import_fitz()
    ordered = sorted({int(page_no) for page_no in page_numbers})
    if not ordered:
        raise PdfRenderError("no pages requested")
    scale = dpi / 72.0
    try:
        with fitz.open(pdf_path) as src:
            out = fitz.open()
            for page_no in ordered:
                if page_no < 1 or page_no > int(src.page_count):
                    out.close()
                    raise PdfRenderError(f"invalid page number: {page_no}")
                src_page = src.load_page(page_no - 1)
                pix = src_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                new_page = out.new_page(width=pix.width, height=pix.height)
                new_page.insert_image(new_page.rect, stream=pix.tobytes("png"))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            out.save(output_path, garbage=4, deflate=True)
            out.close()
        return output_path
    except PdfRenderError:
        raise
    except Exception as exc:
        raise PdfRenderError(f"failed to rasterize pages {ordered}: {exc}") from exc
