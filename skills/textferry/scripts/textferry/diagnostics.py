# SPDX-License-Identifier: AGPL-3.0-only
"""Verify a local runtime without credentials or hosted requests."""
import tempfile
from pathlib import Path
import pymupdf as fitz


def doctor():
    from .workflow import convert
    from .word_export import pandoc_path
    with tempfile.TemporaryDirectory(prefix='textferry-check-') as temporary:
        root = Path(temporary)
        source = root / 'self-test.pdf'
        with fitz.open() as doc:
            doc.new_page().insert_text((40, 60), 'Offline textferry self-test.')
            doc.save(source)
        result = convert(source, root / 'bundle')
        if result['status'] != 'machine_ready' or 'Offline textferry self-test.' not in (root / 'bundle/machine.md').read_text():
            raise ValueError('离线自检没有完成，请先修复运行环境。')
    try:
        pandoc_path()
        optional = True
    except ValueError:
        optional = False
    return {'status': 'ready', 'offline_self_test': True, 'pdf': True, 'markdown': True,
            'epub_component': optional, 'word_component': optional,
            'message': '环境自检通过，可以开始处理材料；需要 OCR 时再配置你自己的令牌。'}
