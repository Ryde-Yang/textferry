# SPDX-License-Identifier: AGPL-3.0-only
"""把工具自带 runtime 加入 Python 导入路径的单一引导函数。"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_runtime_path() -> None:
    runtime_root = Path(__file__).resolve().parent
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))
