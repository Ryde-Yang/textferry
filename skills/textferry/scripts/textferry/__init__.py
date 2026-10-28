# SPDX-License-Identifier: AGPL-3.0-only
"""textferry 包级命名空间。"""

from .service import CreatedJob, TextFerryService, get_textferry_service

__all__ = [
    "CreatedJob",
    "TextFerryService",
    "get_textferry_service",
]
