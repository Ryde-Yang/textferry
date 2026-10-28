# SPDX-License-Identifier: AGPL-3.0-only
"""Keep source illustrations beside directly extracted PDF text."""
import hashlib
from pathlib import Path
import pymupdf as fitz


def page_text_with_figures(page, bundle, number):
    text = page.get_text('text', sort=True)
    regions = [fitz.Rect(info['bbox']) & page.rect for info in page.get_image_info()]
    drawings = [fitz.Rect(item['rect']) & page.rect for item in page.get_drawings() if fitz.Rect(item['rect']).get_area() > 16]
    if drawings:
        combined = fitz.Rect(drawings[0])
        for rect in drawings[1:]:
            combined |= rect
        if not any((combined & rect).get_area() >= combined.get_area() * .95 for rect in regions):
            regions.append(combined)
    unique, records = set(), []
    for rect in regions:
        key = tuple(round(value, 1) for value in rect)
        if key in unique or rect.is_empty:
            continue
        unique.add(key)
        raw = page.get_pixmap(clip=rect, dpi=180, alpha=False).tobytes('png')
        sha = hashlib.sha256(raw).hexdigest()
        relative = 'assets/' + sha[:32] + '.png'
        target = Path(bundle) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError('已有来源图片被修改，请先保留修订，不会覆盖该图片。')
        records.append({'file': relative, 'page': number, 'bbox': list(rect), 'sha256': sha})
    if records:
        text = text.rstrip() + '\n\n' + '\n\n'.join(f'![原页图示 {i}]({row["file"]})' for i, row in enumerate(records, 1)) + '\n'
    return text, records
