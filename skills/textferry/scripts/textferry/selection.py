# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit one-based source ranges, shared by PDF pages and EPUB chapters."""
import re


def parse_selection(value, total):
    if value is None:
        return list(range(1, total + 1))
    selected = []
    for part in value.split(','):
        match = re.fullmatch(r'\s*(\d+)\s*(?:-\s*(\d+)\s*)?', part)
        if not match:
            raise ValueError('范围请写成 1-10 或 1-3,8，编号从 1 开始。')
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= total:
            raise ValueError(f'范围超出材料或顺序有误；可选编号为 1—{total}。')
        selected.extend(range(first, last + 1))
    if selected != sorted(set(selected)):
        raise ValueError('范围需按原文件顺序排列，避免重复编号。')
    return selected
