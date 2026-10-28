#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""新建 textferry 任务的 JSON 摘要。"""

from __future__ import annotations

import sys

from _bootstrap import ensure_runtime_path

ensure_runtime_path()

from textferry.cli import main as cli_main


def main() -> int:
    return cli_main(["create-job", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
