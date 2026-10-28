# SPDX-License-Identifier: AGPL-3.0-only
"""清洗后的文本与质量门禁判断结果。"""

from __future__ import annotations

import re
from dataclasses import dataclass


CONTROL_TOKEN_PATTERNS = (
    "<|begin>",
    "</|grounding>",
    "<fcel>",
    "<nl>",
    "<|end>",
    "</|begin>",
)

CONTROL_REGEX = re.compile(r"</?\|[^>]{1,64}>")
FCEL_REGEX = re.compile(r"</?fcel>", re.IGNORECASE)
NL_REGEX = re.compile(r"<nl>", re.IGNORECASE)
ZERO_WIDTH_REGEX = re.compile(r"[\u200b\u200c\u200d\ufeff]")
CJK_REGEX = re.compile(r"[\u4e00-\u9fff]")
ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")
REPEATED_ASCII_TOKEN_RE = re.compile(r"\b([A-Za-z]{3,})\b(?:\s+\1\b){9,}", re.IGNORECASE)
LONG_SAME_CHAR_RUN_RE = re.compile(r"(.)\1{24,}")
LATEX_WRAPPER_REGEX = re.compile(r"\\(?:underline|uwave|text)\{([^{}]*)\}")
LATEX_SUPERSCRIPT_REGEX = re.compile(r"\$\s*\^\{([^{}]+)\}\s*\$")
LATEX_INLINE_MATH_REGEX = re.compile(r"\$\s*([^$\n]{1,300}?)\s*\$")
LATEX_CMD_REGEX = re.compile(r"\\[A-Za-z]+")
LATEX_ESCAPED_PUNCT_REGEX = re.compile(r"\\([{}$])")


@dataclass(frozen=True)
class OcrQualityResult:
    accepted: bool
    reason: str = ""


def _strip_latex_noise(text: str) -> str:
    cleaned = text
    for _ in range(8):
        updated = LATEX_WRAPPER_REGEX.sub(r"\1", cleaned)
        if updated == cleaned:
            break
        cleaned = updated

    cleaned = LATEX_SUPERSCRIPT_REGEX.sub(r"\1", cleaned)
    cleaned = LATEX_INLINE_MATH_REGEX.sub(r"\1", cleaned)
    cleaned = LATEX_ESCAPED_PUNCT_REGEX.sub(r"\1", cleaned)
    cleaned = LATEX_CMD_REGEX.sub("", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = cleaned.replace("$", "")
    cleaned = cleaned.replace("\\", "")
    return cleaned


def sanitize_ocr_text(text: str) -> str:
    sanitized = text.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = ZERO_WIDTH_REGEX.sub("", sanitized)
    sanitized = NL_REGEX.sub("\n", sanitized)
    sanitized = FCEL_REGEX.sub("", sanitized)
    sanitized = CONTROL_REGEX.sub("", sanitized)
    sanitized = _strip_latex_noise(sanitized)
    sanitized = re.sub(r"[ \t]+", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


def _paddle_quality_check(raw_text: str, cleaned_text: str) -> OcrQualityResult:
    raw = raw_text or ""
    cleaned = cleaned_text or ""

    control_hits = sum(raw.count(token) for token in CONTROL_TOKEN_PATTERNS)
    if control_hits >= 20:
        return OcrQualityResult(False, f"control tokens dominated output (hits={control_hits})")

    if not cleaned.strip():
        return OcrQualityResult(False, "empty text after control-token stripping")

    raw_len = len(raw)
    cleaned_len = len(cleaned)
    angle_ratio = (raw.count("<") + raw.count(">")) / max(raw_len, 1)
    cjk_count = len(CJK_REGEX.findall(cleaned))
    cjk_ratio = cjk_count / max(cleaned_len, 1)

    if raw_len >= 300 and angle_ratio > 0.08 and cjk_ratio < 0.20:
        return OcrQualityResult(
            False,
            f"markup-heavy low-text output (angle_ratio={angle_ratio:.2f}, cjk_ratio={cjk_ratio:.2f})",
        )

    punct_like = sum(1 for ch in cleaned if ch in "，。、,.;:!?！？·-—_[](){}<>|/")
    punct_ratio = punct_like / max(cleaned_len, 1)
    if cleaned_len >= 200 and punct_ratio > 0.70 and cjk_ratio < 0.08:
        return OcrQualityResult(
            False,
            f"punctuation-only output (punct_ratio={punct_ratio:.2f}, cjk_ratio={cjk_ratio:.2f})",
        )

    if REPEATED_ASCII_TOKEN_RE.search(cleaned) and cjk_ratio < 0.15:
        return OcrQualityResult(False, "repeated ASCII token noise dominated output")

    if LONG_SAME_CHAR_RUN_RE.search(cleaned) and cjk_ratio < 0.15:
        return OcrQualityResult(False, "repeated single-character noise dominated output")

    return OcrQualityResult(True)


def assess_ocr_quality(raw_text: str, model_key: str) -> OcrQualityResult:
    cleaned = sanitize_ocr_text(raw_text)
    if model_key == "paddleocr_vl":
        result = _paddle_quality_check(raw_text, cleaned)
        if not result.accepted:
            return result

    if not cleaned:
        return OcrQualityResult(False, "empty OCR output")

    cleaned_len = len(cleaned)
    cjk_count = len(CJK_REGEX.findall(cleaned))
    cjk_ratio = cjk_count / max(cleaned_len, 1)
    ascii_words = ASCII_WORD_RE.findall(cleaned)
    ascii_ratio = sum(len(token) for token in ascii_words) / max(cleaned_len, 1)

    if REPEATED_ASCII_TOKEN_RE.search(cleaned) and cjk_ratio < 0.20:
        return OcrQualityResult(False, "repeated ASCII token noise dominated output")

    if LONG_SAME_CHAR_RUN_RE.search(cleaned) and cjk_ratio < 0.20:
        return OcrQualityResult(False, "repeated single-character noise dominated output")

    if cleaned_len >= 200 and ascii_ratio > 0.75 and cjk_ratio < 0.15:
        return OcrQualityResult(False, "ascii-heavy low-text output")

    return OcrQualityResult(True)
