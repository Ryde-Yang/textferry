# SPDX-License-Identifier: AGPL-3.0-only
"""已决议脚注、待 agent 消歧的冲突页候选、替换占位符后的正文文本与可解释的跳过原因。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List


CIRCLED_NOTE_MARKERS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
NOTE_MARKER_RE = re.compile(rf"([{re.escape(CIRCLED_NOTE_MARKERS)}]|[［【\[]\s*\d{{1,3}}\s*[］】\]])")
NOTE_LINE_RE = re.compile(rf"^\s*(?P<marker>[{re.escape(CIRCLED_NOTE_MARKERS)}]|[［【\[]\s*\d{{1,3}}\s*[］】\]])\s*(?P<text>.+?)\s*$")
TOC_LINE_RE = re.compile(r"[.…·]{2,}\s*[0-9ivxlcdmIVXLCDM一二三四五六七八九十百千]+\s*$")
BODY_MARKER_RE = re.compile(rf"([{re.escape(CIRCLED_NOTE_MARKERS)}]|[［【\[]\s*\d{{1,3}}\s*[］】\]])")
MARKER_NOISE_PREFIX_RE = re.compile(r"^[`~!@#$%^&*+=|:;,.·•※\\-]+")
MAX_SCHOLARLY_NOTE_LENGTH = 800


@dataclass(frozen=True)
class BodyAnchor:
    index: int
    raw_marker: str
    normalized_marker: str
    marker_recovered: bool
    body_snippet: str


@dataclass(frozen=True)
class FootnoteCandidate:
    raw_marker: str
    normalized_marker: str
    text: str
    marker_recovered: bool

    def display_line(self) -> str:
        return f"{self.raw_marker} {self.text}".strip()


@dataclass(frozen=True)
class DetectedFootnote:
    id: int
    marker: str
    normalized_marker: str
    text: str
    placeholder: str
    anchor_index: int
    marker_recovered: bool = False
    layout_assisted: bool = False
    resolution_source: str = "direct_match"


@dataclass(frozen=True)
class FootnoteAgentConflict:
    body_text: str
    definition_block: str
    anchors: List[BodyAnchor]
    note_candidates: List[FootnoteCandidate]
    conflict_reason: str
    layout_assisted: bool = False


@dataclass(frozen=True)
class PageFootnoteDetection:
    attempted: bool
    body_text: str
    resolved_footnotes: List[DetectedFootnote]
    agent_conflicts: List[FootnoteAgentConflict]
    unresolved_lines: List[str]
    skipped_reason: str | None = None
    marker_recovered_notes: int = 0
    layout_assisted_notes: int = 0


def extract_page_footnotes(text: str, *, next_id: int) -> PageFootnoteDetection:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not NOTE_MARKER_RE.search(normalized):
        return PageFootnoteDetection(
            attempted=False,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
        )

    lines = normalized.splitlines()
    nonempty_lines = [line.strip() for line in lines if line.strip()]
    if _looks_like_non_body_page(nonempty_lines):
        return PageFootnoteDetection(
            attempted=True,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
            skipped_reason="non_body_page",
        )

    start_indexes = [idx for idx, line in enumerate(lines) if _parse_note_line(line)[0] is not None]
    if not start_indexes:
        return PageFootnoteDetection(
            attempted=True,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
            skipped_reason="no_definition_block",
        )

    if len(start_indexes) > 5:
        return PageFootnoteDetection(
            attempted=True,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
            skipped_reason="too_many_candidates",
        )

    first_start = start_indexes[0]
    body_text = "\n".join(lines[:first_start]).strip()
    if not body_text:
        return PageFootnoteDetection(
            attempted=True,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
            skipped_reason="missing_inline_anchor",
        )

    parsed = _parse_note_block(lines[first_start:])
    if parsed.skipped_reason is not None:
        return PageFootnoteDetection(
            attempted=True,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
            skipped_reason=parsed.skipped_reason,
        )

    zone_strategy = _assess_definition_zone(lines, first_start, definition_count=len(parsed.footnotes))
    if zone_strategy is None:
        return PageFootnoteDetection(
            attempted=True,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
            skipped_reason="candidate_not_in_bottom_half",
        )

    body_anchors = _find_body_anchors(body_text)
    grouped_anchors: Dict[str, List[BodyAnchor]] = {}
    for anchor in body_anchors:
        grouped_anchors.setdefault(anchor.normalized_marker, []).append(anchor)

    resolved_footnotes: List[DetectedFootnote] = []
    agent_conflicts: List[FootnoteAgentConflict] = []
    unresolved_lines: List[str] = []
    marker_recovered_notes = 0
    layout_assisted_notes = 0
    current_id = next_id
    used_anchor_indexes: set[int] = set()

    for note_candidate in parsed.footnotes:
        anchors = grouped_anchors.get(note_candidate.normalized_marker, [])
        available_anchors = [anchor for anchor in anchors if anchor.index not in used_anchor_indexes]
        if len(available_anchors) > 1:
            agent_conflicts.append(
                FootnoteAgentConflict(
                    body_text=body_text,
                    definition_block=note_candidate.display_line(),
                    anchors=available_anchors,
                    note_candidates=[note_candidate],
                    conflict_reason="ambiguous_inline_anchor",
                    layout_assisted=zone_strategy == "layout_assisted",
                )
            )
            continue
        if not available_anchors:
            unresolved_lines.append(note_candidate.display_line())
            continue

        anchor = available_anchors[0]
        used_anchor_indexes.add(anchor.index)
        placeholder = f"\ue001FN{current_id}\ue002"
        marker_recovered = (
            anchor.raw_marker != note_candidate.raw_marker
            or anchor.marker_recovered
            or note_candidate.marker_recovered
            or note_candidate.normalized_marker != note_candidate.raw_marker
        )
        if marker_recovered:
            marker_recovered_notes += 1
        if zone_strategy == "layout_assisted":
            layout_assisted_notes += 1
        resolved_footnotes.append(
            DetectedFootnote(
                id=current_id,
                marker=note_candidate.raw_marker,
                normalized_marker=note_candidate.normalized_marker,
                text=note_candidate.text,
                placeholder=placeholder,
                anchor_index=anchor.index,
                marker_recovered=marker_recovered,
                layout_assisted=zone_strategy == "layout_assisted",
                resolution_source=zone_strategy,
            )
        )
        current_id += 1

    if not resolved_footnotes and not agent_conflicts:
        return PageFootnoteDetection(
            attempted=True,
            body_text=text,
            resolved_footnotes=[],
            agent_conflicts=[],
            unresolved_lines=[],
            skipped_reason="missing_inline_anchor",
        )

    return PageFootnoteDetection(
        attempted=True,
        body_text=body_text,
        resolved_footnotes=resolved_footnotes,
        agent_conflicts=agent_conflicts,
        unresolved_lines=unresolved_lines,
        marker_recovered_notes=marker_recovered_notes,
        layout_assisted_notes=layout_assisted_notes,
    )


def normalize_marker(raw: str) -> str:
    marker = raw.strip()
    if not marker:
        return marker
    if len(marker) == 1 and marker in CIRCLED_NOTE_MARKERS:
        return marker
    bracket_match = re.fullmatch(r"[［【\[]\s*(\d{1,3})\s*[］】\]]", marker)
    if bracket_match:
        return f"[{int(bracket_match.group(1))}]"
    return marker


def materialize_footnote_text(
    body_text: str,
    footnotes: List[DetectedFootnote],
    *,
    unresolved_lines: List[str] | None = None,
) -> str:
    normalized = body_text or ""
    if not footnotes:
        if unresolved_lines:
            return f"{normalized}\n\n" + "\n".join(unresolved_lines)
        return normalized

    placeholder_by_anchor = {footnote.anchor_index: footnote.placeholder for footnote in footnotes}
    parts: List[str] = []
    cursor = 0
    for anchor_index, match in enumerate(BODY_MARKER_RE.finditer(normalized)):
        parts.append(normalized[cursor : match.start()])
        parts.append(placeholder_by_anchor.get(anchor_index, match.group(0)))
        cursor = match.end()
    parts.append(normalized[cursor:])
    rendered = "".join(parts).strip()
    if unresolved_lines:
        rendered = f"{rendered}\n\n" + "\n".join(unresolved_lines)
    return rendered


def _find_body_anchors(body_text: str) -> List[BodyAnchor]:
    anchors: List[BodyAnchor] = []
    for index, match in enumerate(BODY_MARKER_RE.finditer(body_text)):
        raw_marker = match.group(0)
        anchors.append(
            BodyAnchor(
                index=index,
                raw_marker=raw_marker,
                normalized_marker=normalize_marker(raw_marker),
                marker_recovered=False,
                body_snippet=_body_snippet(body_text, match.start(), match.end()),
            )
        )
    return anchors


@dataclass(frozen=True)
class _ParsedNoteBlock:
    footnotes: List[FootnoteCandidate]
    skipped_reason: str | None = None


def _parse_note_block(lines: List[str]) -> _ParsedNoteBlock:
    parsed: List[FootnoteCandidate] = []
    current_marker: str | None = None
    current_parts: List[str] = []
    current_marker_recovered = False

    def flush_current() -> None:
        nonlocal current_marker, current_parts, current_marker_recovered
        if current_marker is None:
            return
        note_text = " ".join(part.strip() for part in current_parts if part.strip()).strip()
        parsed.append(
            FootnoteCandidate(
                raw_marker=current_marker,
                normalized_marker=normalize_marker(current_marker),
                text=note_text,
                marker_recovered=current_marker_recovered,
            )
        )
        current_marker = None
        current_parts = []
        current_marker_recovered = False

    for line in lines:
        marker, note_text, marker_recovered = _parse_note_line(line)
        if marker is not None:
            flush_current()
            current_marker = marker
            current_parts = [note_text]
            current_marker_recovered = marker_recovered
            continue
        if current_marker is None:
            return _ParsedNoteBlock([], skipped_reason="ambiguous_definition_block")
        stripped = line.strip()
        if not stripped:
            continue
        current_parts.append(stripped)
    flush_current()

    if not parsed:
        return _ParsedNoteBlock([], skipped_reason="no_definition_block")

    markers = [candidate.raw_marker for candidate in parsed]
    if len(set(markers)) != len(markers):
        return _ParsedNoteBlock([], skipped_reason="duplicate_marker")
    if not _is_monotonic(markers):
        return _ParsedNoteBlock([], skipped_reason="unordered_markers")
    if any(len(candidate.text) > MAX_SCHOLARLY_NOTE_LENGTH for candidate in parsed):
        return _ParsedNoteBlock([], skipped_reason="note_too_long")

    return _ParsedNoteBlock(parsed)


def _parse_note_line(line: str) -> tuple[str | None, str, bool]:
    stripped = line.strip()
    trimmed = MARKER_NOISE_PREFIX_RE.sub("", stripped)
    match = NOTE_LINE_RE.match(trimmed)
    if not match:
        return None, "", False
    return match.group("marker"), match.group("text"), trimmed != stripped


def _assess_definition_zone(lines: List[str], start_index: int, *, definition_count: int) -> str | None:
    nonempty_positions = [idx for idx, line in enumerate(lines) if line.strip()]
    if not nonempty_positions:
        return None
    start_rank = sum(1 for idx in nonempty_positions if idx < start_index)
    has_blank_separator = any(not line.strip() for line in lines[max(0, start_index - 2) : start_index])
    strict_threshold = max(1, len(nonempty_positions) // 2)
    if has_blank_separator or start_rank > strict_threshold:
        return "direct_match"

    relaxed_threshold = max(1, len(nonempty_positions) // 3)
    if definition_count >= 2 and start_rank >= relaxed_threshold:
        return "layout_assisted"
    return None


def _looks_like_non_body_page(lines: List[str]) -> bool:
    if not lines:
        return False
    head = "".join(lines[:3])
    if any(token in head for token in ("目录", "参考文献", "附录", "索引")):
        return True
    toc_like_count = sum(1 for line in lines[:8] if TOC_LINE_RE.search(line))
    return toc_like_count >= 2


def _is_monotonic(markers: List[str]) -> bool:
    indexes = [_marker_order(marker) for marker in markers]
    return all(indexes[idx] < indexes[idx + 1] for idx in range(len(indexes) - 1))


def _marker_order(marker: str) -> int:
    if marker in CIRCLED_NOTE_MARKERS:
        return CIRCLED_NOTE_MARKERS.index(marker) + 1
    digits = re.sub(r"[^\d]", "", marker)
    return int(digits or "0")


def _body_snippet(text: str, start: int, end: int, *, radius: int = 16) -> str:
    snippet_start = max(0, start - radius)
    snippet_end = min(len(text), end + radius)
    return text[snippet_start:snippet_end].strip()
