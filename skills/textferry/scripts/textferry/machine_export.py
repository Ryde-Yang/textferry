# SPDX-License-Identifier: AGPL-3.0-only
"""供模型消费的结构化 machine.md 产物，按页保留 SRC、状态和接近 page normalized 的正文块。"""

from __future__ import annotations

from collections import Counter
import re
from pathlib import Path
from typing import Iterable, List

from .docx_export import ExportPage, src_key
from .export_cleanup import clean_machine_text, detect_repeated_noise_lines

MACHINE_SENTENCE_END_RE = re.compile(r"[。！？!?；;：:）】》」』”’)]$")
MACHINE_HEADING_RE = re.compile(r"^(第[0-9一二三四五六七八九十百千]+[章节篇卷回]|[一二三四五六七八九十]+[、．.].*|[一二三四五六七八九十]、.*)$")
MACHINE_STRUCTURAL_LINE_RE = re.compile(r"^(——|【摘要】|【关键词】|\[[^\]]+\]|[（(].*[）)]$)")

def write_machine_markdown(output_path: Path, pages: Iterable[ExportPage], *, preserve_content: bool = False) -> None:
    page_list: List[ExportPage] = list(pages)
    repeated_noise_lines = _detect_machine_repeated_noise_lines([page.text for page in page_list if page.text.strip()])

    chunks: List[str] = []
    for page in page_list:
        chunks.append(_machine_page_header(page))
        chunks.append("")
        chunks.append("```meta")
        chunks.append(f"pdfPageNo: {page.pdf_page_no}")
        chunks.append(f"resolvedLabel: {page.resolved_label}")
        chunks.append(f"src: {src_key(page.pdf_page_no)}")
        chunks.append(f"status: {'failed' if page.failed else 'done'}")
        chunks.append("```")
        chunks.append("")
        if page.failed:
            chunks.append("### Failed Page")
            chunks.append("")
            chunks.append("【该页识别失败，当前 machine 版本保留占位，供后续补跑或下游跳过】")
        else:
            cleaned = page.text.strip() if preserve_content else _clean_machine_page_text(page, repeated_noise_lines)
            chunks.append("### Text")
            chunks.append("")
            chunks.append(cleaned or "【该页没有可导出的机器文本】")
        chunks.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(chunks).strip() + "\n", encoding="utf-8")


def _detect_machine_repeated_noise_lines(texts: List[str]) -> set[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        edge_lines = lines[:3] + lines[-3:]
        for line in edge_lines:
            if len(line) <= 40:
                counter[line] += 1
    repeated = {line for line, count in counter.items() if count >= 2}
    repeated.update(detect_repeated_noise_lines(texts))
    return repeated


def _machine_page_header(page: ExportPage) -> str:
    pdf_label = f"PDF页:{page.pdf_page_no}"
    if page.resolved_label == pdf_label:
        return f"## Page {page.pdf_page_no} | {pdf_label} | SRC:{src_key(page.pdf_page_no)}"
    return f"## Page {page.pdf_page_no} | {page.resolved_label} | {pdf_label} | SRC:{src_key(page.pdf_page_no)}"


def _clean_machine_page_text(page: ExportPage, repeated_noise_lines: set[str]) -> str:
    preserve_lines = _first_page_title_lines(page)
    effective_noise = {line for line in repeated_noise_lines if line not in preserve_lines}
    cleaned = clean_machine_text(page.text, effective_noise)
    blocks = _split_machine_text_blocks(cleaned)
    return "\n\n".join(blocks).strip()


def _first_page_title_lines(page: ExportPage) -> set[str]:
    if page.pdf_page_no != 1:
        return set()
    lines = [line.strip() for line in page.text.splitlines() if line.strip()]
    title_lines: set[str] = set()
    for idx, line in enumerate(lines[:6]):
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        if next_line.startswith("——") or line.startswith("——"):
            title_lines.add(line)
            if next_line:
                title_lines.add(next_line)
    return title_lines


def _split_machine_text_blocks(text: str) -> List[str]:
    raw_blocks = [block for block in re.split(r"\n\s*\n+", text.replace("\r\n", "\n").replace("\r", "\n")) if block.strip()]
    blocks: List[str] = []
    for raw_block in raw_blocks:
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        current = lines[0]
        for line in lines[1:]:
            if _should_merge_machine_line(current, line):
                current = f"{current}{line}"
            else:
                blocks.append(current.strip())
                current = line
        blocks.append(current.strip())
    return [block for block in blocks if block]


def _should_merge_machine_line(previous: str, current: str) -> bool:
    previous = previous.strip()
    current = current.strip()
    if not previous or not current:
        return False
    if MACHINE_SENTENCE_END_RE.search(previous):
        return False
    if _is_machine_structural_line(previous) or _is_machine_structural_line(current):
        return False
    if current.startswith(("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩")):
        return False
    if re.match(r"^[0-9]+[ .．、]", current):
        return False
    return True


def _is_machine_structural_line(line: str) -> bool:
    if MACHINE_STRUCTURAL_LINE_RE.match(line):
        return True
    if MACHINE_HEADING_RE.match(line):
        return True
    if re.fullmatch(r"[一二三四五六七八九十]+", line):
        return True
    if len(line) <= 8 and line.endswith(("序", "篇", "章", "凡例", "目录")):
        return True
    return False
