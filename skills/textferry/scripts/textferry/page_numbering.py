# SPDX-License-Identifier: AGPL-3.0-only
"""标准化书页检测结果与稳定的 resolvedLabel 对象。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .types import PageMode, ResolvedPageLabel

ROMAN_RE = re.compile(r"\b[ivxlcdm]+\b", re.IGNORECASE)
ARABIC_RE = re.compile(r"\b\d{1,4}\b")
CHINESE_RE = re.compile(r"[〇零○一二三四五六七八九十百千两]{1,8}")
PAGE_EXPLICIT_RE = re.compile(
    r"(?:第\s*)?([ivxlcdm]+|\d{1,4}|[〇零○一二三四五六七八九十百千两]{1,8})\s*(?:页|頁|p\.?|page)\b",
    re.IGNORECASE,
)
PAGE_STANDALONE_RE = re.compile(
    r"^(?:第\s*)?([ivxlcdm]+|\d{1,4}|[〇零○一二三四五六七八九十百千两]{1,8})$",
    re.IGNORECASE,
)
BODY_START_RE = re.compile(
    r"(^|\n)\s*(引言|绪论|正文|第一章|第[一二三四五六七八九十百千0-9]+章)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BookPageDetection:
    raw: Optional[str]
    normalized: Optional[int]
    display: Optional[str]


ROMAN_VALUE = {
    "i": 1,
    "v": 5,
    "x": 10,
    "l": 50,
    "c": 100,
    "d": 500,
    "m": 1000,
}

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CHINESE_UNITS = {
    "十": 10,
    "百": 100,
    "千": 1000,
}


def parse_roman(token: str) -> Optional[int]:
    s = token.strip().lower()
    if not s or any(ch not in ROMAN_VALUE for ch in s):
        return None
    total = 0
    prev = 0
    for ch in reversed(s):
        value = ROMAN_VALUE[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    if total <= 0 or total > 3999:
        return None
    return total


def parse_chinese_numeral(token: str) -> Optional[int]:
    s = token.strip()
    if not s:
        return None
    if all(ch in CHINESE_DIGITS for ch in s):
        value = 0
        for ch in s:
            value = value * 10 + CHINESE_DIGITS[ch]
        return value

    total = 0
    section = 0
    number = 0
    for ch in s:
        if ch in CHINESE_DIGITS:
            number = CHINESE_DIGITS[ch]
            continue
        unit = CHINESE_UNITS.get(ch)
        if unit is None:
            return None
        if number == 0:
            number = 1
        section += number * unit
        number = 0

    total += section + number
    if total <= 0:
        return None
    return total


def _candidate_lines(text: str) -> List[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    if len(lines) <= 6:
        return lines
    return lines[:3] + lines[-3:]


def detect_book_page(text: str) -> BookPageDetection:
    for line in _candidate_lines(text):
        if len(line) > 30:
            continue
        match = PAGE_EXPLICIT_RE.search(line)
        if not match and len(line) <= 12:
            match = PAGE_STANDALONE_RE.fullmatch(line)
        if not match:
            continue
        token = match.group(1)
        normalized: Optional[int] = None
        display: Optional[str] = None

        if ARABIC_RE.fullmatch(token):
            normalized = int(token)
            display = token
        elif ROMAN_RE.fullmatch(token):
            parsed = parse_roman(token)
            if parsed is not None:
                normalized = parsed
                display = f"{token.lower()}({parsed})"
        elif CHINESE_RE.fullmatch(token):
            parsed = parse_chinese_numeral(token)
            if parsed is not None:
                normalized = parsed
                display = f"{token}({parsed})"

        if normalized is not None and 0 < normalized < 10000:
            return BookPageDetection(raw=token, normalized=normalized, display=display)

    return BookPageDetection(raw=None, normalized=None, display=None)


def _book_label(display: str, value: int) -> str:
    return f"书页:{display or value}"


def _pdf_fallback_label(pdf_page_no: int) -> str:
    return f"PDF页:{pdf_page_no} (书页回退)"


def resolve_book_mode_labels(
    pdf_page_numbers: Iterable[int],
    raw_values: Iterable[Optional[str]],
    normalized_values: Iterable[Optional[int]],
    displays: Iterable[Optional[str]],
) -> List[ResolvedPageLabel]:
    pdf_pages = list(pdf_page_numbers)
    _ = list(raw_values)
    nums = list(normalized_values)
    display_values = list(displays)
    total = len(pdf_pages)

    labels: List[Optional[ResolvedPageLabel]] = [None] * total
    anchors = [(idx, nums[idx]) for idx in range(total) if nums[idx] is not None]

    for idx, num in anchors:
        assert num is not None
        display = display_values[idx] or str(num)
        labels[idx] = ResolvedPageLabel(
            pdf_page_no=pdf_pages[idx],
            display=_book_label(display, num),
            mode=PageMode.BOOK,
            value=num,
            fallback=False,
        )

    for i in range(len(anchors) - 1):
        left_idx, left_num = anchors[i]
        right_idx, right_num = anchors[i + 1]
        assert left_num is not None and right_num is not None
        idx_delta = right_idx - left_idx
        num_delta = right_num - left_num
        if idx_delta <= 1 or idx_delta != num_delta:
            continue
        for offset in range(1, idx_delta):
            pos = left_idx + offset
            inferred_value = left_num + offset
            labels[pos] = ResolvedPageLabel(
                pdf_page_no=pdf_pages[pos],
                display=f"书页:{inferred_value}",
                mode=PageMode.BOOK,
                value=inferred_value,
                fallback=False,
            )

    resolved: List[ResolvedPageLabel] = []
    for idx, pdf_page_no in enumerate(pdf_pages):
        if labels[idx] is not None:
            resolved.append(labels[idx] or ResolvedPageLabel(pdf_page_no, "", PageMode.PDF, pdf_page_no, True))
            continue
        resolved.append(
            ResolvedPageLabel(
                pdf_page_no=pdf_page_no,
                display=_pdf_fallback_label(pdf_page_no),
                mode=PageMode.PDF,
                value=pdf_page_no,
                fallback=True,
            )
        )
    return resolved


def detect_body_start(pdf_pages: Iterable[int], normalized_texts: Iterable[str]) -> Optional[int]:
    for pdf_page_no, text in zip(pdf_pages, normalized_texts):
        if BODY_START_RE.search(text or ""):
            return pdf_page_no
    return None


def resolve_labels(
    mode: PageMode,
    pdf_page_numbers: Iterable[int],
    raw_values: Iterable[Optional[str]],
    normalized_values: Iterable[Optional[int]],
    displays: Iterable[Optional[str]],
    body_start_pdf_page: Optional[int],
) -> List[ResolvedPageLabel]:
    pdf_pages = list(pdf_page_numbers)
    if mode == PageMode.PDF:
        return [
            ResolvedPageLabel(
                pdf_page_no=page_no,
                display=f"PDF页:{page_no}",
                mode=PageMode.PDF,
                value=page_no,
                fallback=False,
            )
            for page_no in pdf_pages
        ]

    if mode == PageMode.BOOK:
        return resolve_book_mode_labels(pdf_pages, raw_values, normalized_values, displays)

    resolved: List[ResolvedPageLabel] = []
    for pdf_page_no in pdf_pages:
        if body_start_pdf_page is not None and pdf_page_no >= body_start_pdf_page:
            value = pdf_page_no - body_start_pdf_page + 1
            resolved.append(
                ResolvedPageLabel(
                    pdf_page_no=pdf_page_no,
                    display=f"正文页:{value}",
                    mode=PageMode.BODY,
                    value=value,
                    fallback=False,
                )
            )
        else:
            resolved.append(
                ResolvedPageLabel(
                    pdf_page_no=pdf_page_no,
                    display=f"PDF页:{pdf_page_no} (正文前)",
                    mode=PageMode.PDF,
                    value=pdf_page_no,
                    fallback=True,
                )
            )
    return resolved
