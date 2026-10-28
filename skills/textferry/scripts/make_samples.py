#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Generate original, small Chinese fixtures; no private research material."""

import argparse
import json
from pathlib import Path

import pymupdf as fitz


def put_text(page, point, text, size=12):
    writer = fitz.TextWriter(page.rect)
    writer.append(point, text, font=fitz.Font("cjk"), fontsize=size)
    writer.write_text(page)


def text_page(doc, title, lines, note):
    page = doc.new_page(width=595, height=842)
    put_text(page, (50, 70), title, 18)
    for index, line in enumerate(lines):
        put_text(page, (50, 125 + index * 30), line)
    put_text(page, (50, 730), note, 9)
    return page


def generate(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        text_page(doc, "材料整理示例：来源与层次", ["此處保留繁體原文，不改寫作者的論證。", "正文和引文需要分清，脚注也应保留。[1]", "公式 $x^{2}$ 与链接 https://example.org 应留在底稿。"], "[1] 本页全部为自制测试材料，不是正式文献引文。")
        text_page(doc, "第二节 核查范围", ["转换完成只说明取得机器底稿。", "核查者需要说明检查了哪些页面和内容。"], "[2] 自制脚注示例。")
        doc.save(output / "paper-text.pdf")
    with fitz.open() as doc:
        text_page(doc, "第一章 来源", ["卷章顺序服从原书，机器抽取仍需核对。", "现代研究著作的作者分析不能压缩成摘要。"], "自制图书版式示例。")
        text_page(doc, "第二章 注释", ["章首、章尾和注释归属需要分别核对。", "本示例用于验证处理步骤，不模拟整书准出。"], "自制图书版式示例。")
        doc.save(output / "book-text.pdf")
    with fitz.open() as original:
        page = original.new_page(width=595, height=842)
        put_text(page, (80, 65), "古籍版式示例（自制）", 18)
        columns = ["孟子曰仁者愛人有禮者敬人", "注此為保留原文與注釋層次之測試", "疏此頁為自製材料不作校勘依據"]
        for col, text in enumerate(columns):
            for row, char in enumerate(text):
                put_text(page, (480 - col * 120, 130 + row * 28), char, 16 if col == 0 else 12)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        with fitz.open() as scan:
            scan.new_page(width=595, height=842).insert_image(page.rect, stream=pix.tobytes("png"))
            scan.save(output / "classical-scan.pdf")
    expected = {"paper-text.pdf": ["此處", "$x^{2}$", "https://example.org", "[1]"], "book-text.pdf": ["第一章", "第二章"], "classical-scan.pdf": columns}
    (output / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    print(generate(parser.parse_args().output))
