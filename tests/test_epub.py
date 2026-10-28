"""EPUB behavior with a small authored fixture, without OCR/network calls."""
import json
from pathlib import Path
from zipfile import ZipFile

import pymupdf as fitz
import pytest

from textferry import workflow
from textferry.handwash import prepare_handwash, export_handwash


@pytest.fixture
def epub(tmp_path):
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    image.clear_with(200)
    path = tmp_path / '自制.epub'
    with ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        z.writestr('OPS/book.opf', '''<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">synthetic</dc:identifier><dc:title>自制书</dc:title><dc:language>zh</dc:language></metadata><manifest><item id="b" href="second.xhtml" media-type="application/xhtml+xml"/><item id="a" href="first.xhtml" media-type="application/xhtml+xml"/><item id="pic" href="images/a.png" media-type="image/png"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>''')
        z.writestr('OPS/second.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>后章</title></head><body><h1>第二章</h1><p>后章正文。</p><aside epub:type="footnote" id="n1"><p>跨章注释原文。</p></aside></body></html>')
        z.writestr('OPS/first.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>前章</title></head><body><h1>第一章</h1><p>此處繁體文字。<a epub:type="noteref" href="second.xhtml#n1">1</a></p><p><img src="images/a.png" alt="自制插图"/></p><table><tr><th>数</th></tr><tr><td>42</td></tr></table><p><math xmlns="http://www.w3.org/1998/Math/MathML"><msup><mi>x</mi><mn>2</mn></msup></math></p></body></html>''')
        z.writestr('OPS/images/a.png', image.tobytes('png'))
    return path


def test_epub_spine_images_notes_math_and_source_are_preserved(epub, tmp_path, monkeypatch):
    from textferry.service import TextFerryService
    monkeypatch.setattr(TextFerryService, 'run_job', lambda *a, **k: pytest.fail('EPUB text must not call OCR'))
    bundle = tmp_path / 'bundle'
    result = workflow.convert(epub, bundle, kind='book')
    text = (bundle / 'machine.md').read_text()
    assert result['status'] == 'machine_ready'
    assert result['source_format'] == 'epub'
    assert result['source_unit'] == 'chapter'
    assert text.index('第一章') < text.index('第二章')
    assert '此處繁體' in text and '跨章注释原文' in text
    assert '42' in text and '$x^{2}$' in text
    assert '[^e1-1]' in text
    assert '跨章注释原文' in (bundle / 'source/chapters/0001.xhtml').read_text()
    assert 'PDF页' not in text
    assert (bundle / result['source_file']).read_bytes() == epub.read_bytes()
    assert list((bundle / 'assets').rglob('*.png'))
    assert 'assets/' in text


def test_epub_review_uses_chapter_sources_and_can_export(epub, tmp_path):
    bundle = tmp_path / 'review'
    workflow.convert(epub, bundle, mode='review', kind='book')
    prepare_handwash(bundle)
    layout = json.loads((bundle / 'handwash/layout.json').read_text())
    for row in layout['pages']:
        row['boundary'] = 'separate'
        workflow.review_page(bundle, row['pdf_page'], checks='body,order,notes,structure', reviewer='test', note='Synthetic source comparison', issues=[])
    workflow.write_json(bundle / 'handwash/layout.json', layout)
    result = export_handwash(bundle, reviewer='test', note='Synthetic assembly', page_numbers='show')
    assert result['current']
    final = (bundle / 'handwashed.md').read_text()
    assert 'EPUB 第 1 节' in final and 'PDF 第' not in final
    assert '章节' in (bundle / '整理说明.md').read_text()


@pytest.mark.parametrize('kind', ['traversal', 'encrypted', 'external', 'image_only'])
def test_epub_rejects_unsafe_or_unhandled_input(epub, tmp_path, kind):
    with ZipFile(epub) as z:
        files = {name: z.read(name) for name in z.namelist()}
    if kind == 'traversal':
        files['../escape.txt'] = b'escape'
    elif kind == 'encrypted':
        files['META-INF/encryption.xml'] = b'<encryption/>'
    elif kind == 'external':
        files['OPS/first.xhtml'] = files['OPS/first.xhtml'].replace(b'images/a.png', b'https://example.test/private.png')
    else:
        for filename in ('OPS/first.xhtml', 'OPS/second.xhtml'):
            files[filename] = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="images/a.png"/></body></html>'
    with ZipFile(epub, 'w') as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    with pytest.raises(ValueError):
        workflow.convert(epub, tmp_path / 'blocked', kind='book')
    assert not (tmp_path / 'escape.txt').exists()


def test_epub_word_keeps_images_table_and_cross_chapter_footnote(epub, tmp_path):
    from xml.etree import ElementTree as ET
    from textferry.word_export import write_word
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle)
    target = bundle / 'basic.docx'
    write_word((bundle / 'machine.md').read_text(), target, bundle)
    with ZipFile(target) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        m = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
        assert root.find('.//' + w + 'tbl') is not None
        assert root.find('.//' + m + 'oMath') is not None
        assert len([n for n in z.namelist() if n.startswith('word/media/')]) == 1
        assert '跨章注释原文' in z.read('word/footnotes.xml').decode()


def test_epub_changed_source_asset_invalidates_review(epub, tmp_path):
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle, mode='review')
    workflow.review_page(bundle, 1, checks='body,order,notes,structure', reviewer='test', note='Synthetic review', issues=[])
    next((bundle / 'assets').rglob('*.png')).write_bytes(b'changed image')
    root, manifest = workflow.read_manifest(bundle)
    result = workflow.collect_report(root, manifest, workflow.engine(root, manifest))
    assert result['stale_review_pages'] == [1]


def test_epub_image_cover_is_preserved_without_claiming_its_text_extracted(epub, tmp_path):
    with ZipFile(epub) as z:
        files = {name: z.read(name) for name in z.namelist()}
    files['OPS/first.xhtml'] = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="images/a.png"/></body></html>'
    with ZipFile(epub, 'w') as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    result = workflow.convert(epub, tmp_path / 'bundle')
    assert result['status'] == 'partial'
    assert result['image_only_chapters'] == [1]
    assert 'assets/' in (tmp_path / 'bundle/machine.md').read_text()


def test_epub_is_not_mislabeled_as_pdf_in_share_candidate(epub, tmp_path):
    from textferry.sharing import prepare_share
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle)
    with pytest.raises(ValueError, match='EPUB'):
        prepare_share(bundle, tmp_path / 'unused-rights.json', tmp_path / 'candidate')


def test_epub_internal_chapter_links_keep_a_real_target(epub, tmp_path):
    with ZipFile(epub) as z:
        files = {name: z.read(name) for name in z.namelist()}
    files['OPS/first.xhtml'] = files['OPS/first.xhtml'].replace(b'</body>', '<p><a href="second.xhtml">跳到后章</a></p></body>'.encode())
    with ZipFile(epub, 'w') as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle)
    import re
    text = (bundle / 'machine.md').read_text()
    target = re.search(r'\[跳到后章\]\(#([^\)]+)\)', text)
    assert target is not None
    assert '{#' + target[1] + '}' in text


def rewrite_epub(path, replacements):
    with ZipFile(path) as z:
        files = {name: z.read(name) for name in z.namelist()}
    for name, transform in replacements.items():
        files[name] = transform(files[name])
    with ZipFile(path, 'w') as z:
        for name, raw in files.items():
            z.writestr(name, raw)


def test_note_tail_stays_in_body_once(epub, tmp_path):
    rewrite_epub(epub, {'OPS/second.xhtml': lambda raw: raw.replace(b'</aside>', b'</aside>BODY_AFTER_NOTE_SENTINEL')})
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle)
    text = (bundle / 'machine.md').read_text()
    assert text.count('BODY_AFTER_NOTE_SENTINEL') == 1


def test_epub_literal_note_syntax_in_code_is_unchanged(epub, tmp_path):
    rewrite_epub(epub, {'OPS/first.xhtml': lambda raw: raw.replace(b'<h1>', b'<pre><code>[^1]: LITERAL_CODE</code></pre><p><code>[^1]</code></p><h1>', 1)})
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle)
    text = (bundle / 'machine.md').read_text()
    assert '    [^1]: LITERAL_CODE' in text
    assert '`[^1]`' in text
    assert '[^e1-1]: 跨章注释原文' in text


def test_epub_note_backlink_has_a_target(epub, tmp_path):
    import re
    rewrite_epub(epub, {
        'OPS/first.xhtml': lambda raw: raw.replace(b'<a epub:type="noteref"', b'<a id="ref1" epub:type="noteref"'),
        'OPS/second.xhtml': lambda raw: raw.replace('</p></aside>'.encode(), '<a href="first.xhtml#ref1">返回</a></p></aside>'.encode()),
    })
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle)
    text = (bundle / 'machine.md').read_text()
    link = re.search(r'\[返回\]\(#([^\)]+)\)', text)
    assert link and '{#' + link[1] + '}' in text


def test_epub_inline_svg_exports_to_word(epub, tmp_path):
    rewrite_epub(epub, {'OPS/first.xhtml': lambda raw: raw.replace(b'</body>', b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><text x="10" y="30">Authored SVG</text><path d="M0 0 L100 100"/></svg></body>')})
    bundle = tmp_path / 'bundle'
    result = workflow.convert(epub, bundle, output_format='both')
    assert result['status'] == 'machine_ready'
    with ZipFile(bundle / '基础稿.docx') as z:
        assert len([name for name in z.namelist() if name.startswith('word/media/')]) >= 2
    assert 'data:image/' not in (bundle / 'machine.md').read_text()
    assert list((bundle / 'assets').glob('*.svg'))


def test_interrupted_epub_resumes_without_ocr_or_losing_drafts(epub, tmp_path, monkeypatch):
    from textferry.storage import LocalTextFerryStore
    from textferry.service import TextFerryService
    original = LocalTextFerryStore.mark_page_done
    def interrupted(self, job_id, page_no, **kwargs):
        if page_no == 2:
            raise KeyboardInterrupt()
        return original(self, job_id, page_no, **kwargs)
    bundle = tmp_path / 'bundle'
    with monkeypatch.context() as context:
        context.setattr(LocalTextFerryStore, 'mark_page_done', interrupted)
        with pytest.raises(KeyboardInterrupt):
            workflow.convert(epub, bundle, mode='review')
    monkeypatch.setattr(TextFerryService, 'run_job', lambda *a, **k: pytest.fail('EPUB resume must not call OCR'))
    if (bundle / 'review/pages/0001.md').exists():
        (bundle / 'review/pages/0001.md').write_text('Keep user edits.')
    result = workflow.resume(bundle)
    assert result['status'] == 'awaiting_review'
    assert result['processed_pages'] == 2
    assert '后章正文' in (bundle / 'machine.md').read_text()


def test_epub_chapter_selection_remembers_original_numbers(epub, tmp_path):
    bundle = tmp_path / 'bundle'
    result = workflow.convert(epub, bundle, chapters='2')
    assert result['processed_pages'] == 1
    assert result['source_chapter_map'] == [2]
    assert '第一章' not in (bundle / 'machine.md').read_text()
    assert 'EPUB 第 2 节' in (bundle / 'machine.md').read_text()
    assert '跨章注释原文' in (bundle / 'machine.md').read_text()


def test_epub_code_note_syntax_survives_review_export(epub, tmp_path):
    rewrite_epub(epub, {'OPS/first.xhtml': lambda raw: raw.replace(b'<h1>', b'<pre><code>[^literal]: SAMPLE</code></pre><h1>', 1)})
    bundle = tmp_path / 'bundle'
    workflow.convert(epub, bundle, mode='review')
    prepare_handwash(bundle)
    layout = json.loads((bundle / 'handwash/layout.json').read_text())
    for row in layout['pages']:
        row['boundary'] = 'separate'
        workflow.review_page(bundle, row['pdf_page'], checks='body,order,notes,structure', reviewer='test', note='Authored code and notes.', issues=[])
    workflow.write_json(bundle / 'handwash/layout.json', layout)
    result = export_handwash(bundle, reviewer='test', note='Authored fixture.')
    assert result['current']
    assert '    [^literal]: SAMPLE' in (bundle / 'handwashed.md').read_text()


def test_epub_missing_body_reports_the_malformed_chapter(epub, tmp_path):
    with ZipFile(epub) as z:
        files = {name: z.read(name) for name in z.namelist()}
    files['OPS/first.xhtml'] = b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Broken chapter</title></head></html>'
    with ZipFile(epub, 'w') as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    with pytest.raises(ValueError, match='正文.*first.xhtml'):
        workflow.inspect_material(epub)
    assert not (tmp_path / 'blocked').exists()
