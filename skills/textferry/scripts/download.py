#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""已复制产物的位置 JSON。"""

from __future__ import annotations

import sys

from _bootstrap import ensure_runtime_path

ensure_runtime_path()

from textferry.cli import main as cli_main


def main() -> int:
    return cli_main(["download", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
