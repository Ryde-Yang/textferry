# SPDX-License-Identifier: AGPL-3.0-only
"""去噪、规整、繁简转换、卷次页眉剔除与病态重复串压缩后的稳定文本。"""

from __future__ import annotations

import re
from functools import lru_cache

from .types import ScriptTarget

MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
PAGE_NUMBER_LINE_RE = re.compile(r"^\s*[0-9]{1,4}\s*$")
SECTION_PAGE_HEADER_RE = re.compile(r"^\s*(导言|導言|例言|目次|目\s*次)\s*[0-9]{1,4}\s*$")
BOOK_TITLE_ONLY_RE = re.compile(r"^\s*孟子\s*[译譯]\s*[注註]\s*$")
BOOK_TITLE_PREFIX_RE = re.compile(r"^\s*(?:[0-9]{1,4}\s*)?孟子\s*[译譯]\s*[注註]\s*(.*)$")
CLASSICAL_BOOK_TITLE_ONLY_RE = re.compile(r"^\s*孟子注疏(?:解經|解经)?\s*$")
CLASSICAL_VOLUME_ONLY_RE = re.compile(r"^\s*卷第[〇零一二三四五六七八九十百千0-9]+[上下中]?\s*$")
CLASSICAL_BOOK_VOLUME_RE = re.compile(
    r"^\s*孟子注疏(?:解經|解经)?卷第[〇零一二三四五六七八九十百千0-9]+[上下中]?\s*$"
)
CJK_INNER_SPACE_RE = re.compile(r"([\u4e00-\u9fff])[ \t]+([\u4e00-\u9fff])")
SPACE_BEFORE_CJK_PUNCT_RE = re.compile(r"[ \t]+([，。！？；：、）】》」』])")
SPACE_AFTER_CJK_PUNCT_BEFORE_CJK_RE = re.compile(r"([，。！？；：、])[ \t]+([\u4e00-\u9fff])")
SPACE_AFTER_CJK_OPEN_PUNCT_RE = re.compile(r"([（【《「『])[ \t]+")
CLAUSE_UNIT_RE = re.compile(r"[^\n；。！？!?]+[；。！？!?]?|\n")


@lru_cache(maxsize=4)
def _opencc_converter(config_name: str):
    try:
        from opencc import OpenCC  # type: ignore
    except Exception:
        return None
    return OpenCC(config_name)


def _convert_script(text: str, target: ScriptTarget) -> str:
    if target == ScriptTarget.ORIGINAL:
        return text

    if target == ScriptTarget.SIMP:
        converter = _opencc_converter("t2s")
    else:
        converter = _opencc_converter("s2t")

    if converter is None:
        return text
    try:
        return converter.convert(text)
    except Exception:
        return text


def _collapse_vertical_layout(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines:
        return ""

    non_empty = [line.strip() for line in lines if line.strip()]
    if len(non_empty) < 5:
        return text.strip()

    single_char_count = sum(1 for line in non_empty if len(line) <= 1)
    if single_char_count / max(len(non_empty), 1) < 0.7:
        return text.strip()

    joined = "".join(non_empty)
    return joined.strip()


def _strip_running_header_footer(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    non_empty_indices = [idx for idx, line in enumerate(lines) if line.strip()]
    if not non_empty_indices:
        return ""

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if (
            CLASSICAL_BOOK_TITLE_ONLY_RE.match(line)
            or CLASSICAL_VOLUME_ONLY_RE.match(line)
            or CLASSICAL_BOOK_VOLUME_RE.match(line)
        ):
            lines[idx] = ""

    for idx in non_empty_indices[:3]:
        line = lines[idx].strip()
        if (
            PAGE_NUMBER_LINE_RE.match(line)
            or SECTION_PAGE_HEADER_RE.match(line)
            or BOOK_TITLE_ONLY_RE.match(line)
        ):
            lines[idx] = ""
            continue

        match = BOOK_TITLE_PREFIX_RE.match(line)
        if match:
            lines[idx] = match.group(1).strip()

    for idx in non_empty_indices[-2:]:
        line = lines[idx].strip()
        if PAGE_NUMBER_LINE_RE.match(line):
            lines[idx] = ""

    return "\n".join(lines).strip()


def _collapse_pathological_repeated_clauses(text: str) -> str:
    units = CLAUSE_UNIT_RE.findall(text)
    if not units:
        return text

    collapsed: list[str] = []
    idx = 0
    total_units = len(units)
    while idx < total_units:
        unit = units[idx]
        if unit == "\n":
            collapsed.append(unit)
            idx += 1
            continue

        repeat_end = idx + 1
        while repeat_end < total_units and units[repeat_end] == unit:
            repeat_end += 1

        repeat_count = repeat_end - idx
        if _should_collapse_repeated_clause(unit, repeat_count):
            collapsed.append(unit)
        else:
            collapsed.extend(units[idx:repeat_end])
        idx = repeat_end

    return "".join(collapsed)


def _should_collapse_repeated_clause(unit: str, repeat_count: int) -> bool:
    stripped = unit.strip()
    if repeat_count < 4:
        return False
    if len(stripped) < 16 or len(stripped) > 120:
        return False
    cjk_count = sum(1 for ch in stripped if "\u4e00" <= ch <= "\u9fff")
    if cjk_count < 8:
        return False
    return cjk_count / max(len(stripped), 1) >= 0.45


def _normalize_punctuation(text: str) -> str:
    normalized = text
    normalized = normalized.replace("｡", "。")
    normalized = normalized.replace("﹐", "，").replace("﹒", "。").replace("﹔", "；")
    normalized = normalized.replace("﹖", "？").replace("﹗", "！").replace("﹕", "：")
    normalized = normalized.replace("﹙", "（").replace("﹚", "）")
    normalized = normalized.replace("﹛", "{").replace("﹜", "}")
    normalized = re.sub(r"[\r\f\v]+", "\n", normalized)
    normalized = MULTI_SPACE_RE.sub(" ", normalized)
    normalized = CJK_INNER_SPACE_RE.sub(r"\1\2", normalized)
    normalized = SPACE_BEFORE_CJK_PUNCT_RE.sub(r"\1", normalized)
    normalized = SPACE_AFTER_CJK_PUNCT_BEFORE_CJK_RE.sub(r"\1\2", normalized)
    normalized = SPACE_AFTER_CJK_OPEN_PUNCT_RE.sub(r"\1", normalized)
    normalized = _collapse_pathological_repeated_clauses(normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def normalize_ocr_text(text: str, script_target: ScriptTarget, *, preserve_content: bool = False) -> str:
    if preserve_content:
        return _convert_script(text.replace("\r\n", "\n").replace("\r", "\n"), script_target)
    prepared = _collapse_vertical_layout(text)
    converted = _convert_script(prepared, script_target)
    without_header = _strip_running_header_footer(converted)
    return _normalize_punctuation(without_header)
