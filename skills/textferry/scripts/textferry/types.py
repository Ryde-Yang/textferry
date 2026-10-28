# SPDX-License-Identifier: AGPL-3.0-only
"""供 storage/service/page_numbering 共享的稳定类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class PageMode(str, Enum):
    PDF = "pdf"
    BOOK = "book"
    BODY = "body"


class ScriptTarget(str, Enum):
    SIMP = "simp"
    TRAD = "trad"
    ORIGINAL = "original"


class JobStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_BODY_START = "WAITING_BODY_START"
    PARTIAL_READY = "PARTIAL_READY"
    FINAL_READY = "FINAL_READY"
    FAILED = "FAILED"


class PageStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ModelOption:
    key: str
    display_name: str
    model_id: str
    prompt_template: str
    stop_tokens: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedPageLabel:
    pdf_page_no: int
    display: str
    mode: PageMode
    value: int
    fallback: bool
