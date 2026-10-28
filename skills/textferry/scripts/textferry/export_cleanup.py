# SPDX-License-Identifier: AGPL-3.0-only
"""去掉封面页噪声、重复页眉、机器版期刊页脚、中文页码残影、校勘注残留与装饰符后的稳定导出文本块，以及 flow 模型复检触发信号。"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Sequence, Set


PUNCT_ONLY_RE = re.compile(r"^[\W_·•⋯…！？!、，,;；:：\-=—~]{1,12}$")
ASCII_CODE_RE = re.compile(r"^[A-Za-z]{0,4}[0-9A-Za-z.\-]{2,20}$")
PAGE_CODE_RE = re.compile(r"^[A-Z]?[0-9]{1,4}(?:\.[0-9]{1,4})?$")
TOO_SHORT_NOISE_RE = re.compile(r"^[A-Za-z0-9.\-]{1,8}$")
HTML_TABLE_RE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)
FLOW_STANDALONE_CJK_PAGE_LABEL_RE = re.compile(r"^[〇零一二三四五六七八九]{2,4}$")
FLOW_INLINE_CJK_PAGE_LABEL_RE = re.compile(r"(?<=[\u4e00-\u9fff])([〇零一二三四五六七八九]{3,4})(?=[\u4e00-\u9fff])")
FLOW_NOTE_MARKER_PREFIX_RE = re.compile(r"^\s*(?:[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|[［【\[]\s*\d{1,3}\s*[］】\]])")
FLOW_TEXTUAL_CRITICISM_RE = re.compile(
    r"(阮校|音义|原作|原无|据补|衍文|脱字|脱「|作「|作“|"
    r"闽、?监、?毛本|孔本|韩本|考文古本|岳本|廖本|宋本|孟子正义同)"
)
PUBLISHER_FOOTER_RE = re.compile(
    r"(all rights reserved|electronic publishing house|https?://|www\.[a-z0-9.-]+)",
    re.IGNORECASE,
)
JOURNAL_HEADER_NOISE_RE = re.compile(r"(journal|学报\(社会科学版\)|年第\d+期|No\.\d+)", re.IGNORECASE)


def detect_repeated_noise_lines(texts: Sequence[str]) -> Set[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines[:3]:
            if len(line) <= 20:
                counter[line] += 1
    return {line for line, count in counter.items() if count >= 3}


def clean_machine_text(text: str, repeated_noise_lines: Set[str]) -> str:
    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        if line in repeated_noise_lines:
            continue
        if JOURNAL_HEADER_NOISE_RE.search(line):
            continue
        if PUBLISHER_FOOTER_RE.search(line):
            continue
        if PUNCT_ONLY_RE.match(line):
            continue
        if PAGE_CODE_RE.match(line):
            continue
        if TOO_SHORT_NOISE_RE.match(line) and not _has_cjk(line):
            continue
        if ASCII_CODE_RE.match(line) and not _has_cjk(line):
            continue
        cleaned_lines.append(line)
    return _collapse_blank_lines("\n".join(cleaned_lines).strip())


def clean_reader_text(text: str, repeated_noise_lines: Set[str]) -> str:
    cleaned = clean_machine_text(text, repeated_noise_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_flow_reader_text(text: str, repeated_noise_lines: Set[str]) -> str:
    cleaned = clean_reader_text(text, repeated_noise_lines)
    flow_lines: List[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            flow_lines.append("")
            continue
        if _is_standalone_flow_page_label(line):
            continue
        if _is_flow_textual_criticism_line(line):
            continue
        flow_lines.append(line.strip())
    return _collapse_blank_lines("\n".join(flow_lines).strip())


def detect_flow_model_polish_signals(text: str) -> dict[str, int]:
    standalone_labels = 0
    inline_labels = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _is_standalone_flow_page_label(line):
            standalone_labels += 1
        inline_labels += len(FLOW_INLINE_CJK_PAGE_LABEL_RE.findall(line))
    signals = {
        "standalonePageLabels": standalone_labels,
        "inlinePageLabels": inline_labels,
    }
    return {key: value for key, value in signals.items() if value > 0}


def _split_reader_blocks(text: str) -> List[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]


def iter_blocks_preserving_tables(text: str) -> Iterable[tuple[str, str]]:
    blocks = _split_reader_blocks(text)
    for block in blocks:
        if HTML_TABLE_RE.fullmatch(block):
            yield "table", block
            continue
        cursor = 0
        found_table = False
        for match in HTML_TABLE_RE.finditer(block):
            before = block[cursor : match.start()].strip()
            if before:
                yield "text", before
            yield "table", match.group(0)
            cursor = match.end()
            found_table = True
        if found_table:
            after = block[cursor:].strip()
            if after:
                yield "text", after
            continue
        else:
            yield "text", block


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _is_standalone_flow_page_label(line: str) -> bool:
    return bool(FLOW_STANDALONE_CJK_PAGE_LABEL_RE.fullmatch(line))


def _is_flow_textual_criticism_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if FLOW_NOTE_MARKER_PREFIX_RE.match(stripped):
        return True
    if FLOW_TEXTUAL_CRITICISM_RE.search(stripped):
        return True
    return False
