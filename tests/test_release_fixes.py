"""Regressions reproduced during the release audit; synthetic inputs only."""
import asyncio
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import httpx
import pymupdf as fitz
import pytest

from textferry import workflow
from textferry.word_export import write_word
from textferry.ocr_client import BaiduAsyncProvider, OcrClientError
from textferry.config import get_textferry_settings, get_model_registry


def text_pdf(path):
    with fitz.open() as doc:
        doc.new_page().insert_text((40, 60), 'Synthetic user-owned material.')
        doc.save(path)
    return path


def test_raster_body_with_long_footer_requires_ocr(tmp_path):
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 600, 700), False)
    image.clear_with(235)
    path = tmp_path / 'hybrid.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_image(fitz.Rect(15, 15, 580, 710), stream=image.tobytes('png'))
        page.insert_textbox(fitz.Rect(15, 730, 580, 830), 'Footer and publisher information ' * 8, fontsize=10)
        doc.save(path)
    result = workflow.inspect_pdf(path)
    assert result['pages'][0]['characters'] > 100
    assert result['ocr_pages'] == [1]


@pytest.mark.parametrize('action', ['report', 'resume'])
def test_basic_markdown_user_edits_survive(tmp_path, action):
    bundle = tmp_path / 'bundle'
    workflow.convert(text_pdf(tmp_path / 'test.pdf'), bundle)
    target = bundle / 'machine.md'
    target.write_text(target.read_text() + '\nUSER EDIT\n')
    if action == 'resume':
        result = workflow.resume(bundle)
    else:
        root, manifest = workflow.read_manifest(bundle)
        result = workflow.export_bundle(root, manifest, workflow.engine(root, manifest))
    assert 'USER EDIT' in target.read_text()
    assert result['machine_user_modified'] is True


def test_html_table_becomes_word_table(tmp_path):
    target = tmp_path / 'table.docx'
    write_word('前文\n\n<table><tr><th>项目</th><th>数值</th></tr><tr><td>A</td><td>42</td></tr></table>\n\n后文', target, tmp_path)
    with ZipFile(target) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    table = root.find('.//' + ns + 'tbl')
    assert table is not None
    assert '42' in ''.join(table.itertext())
    assert '<table>' not in ''.join(root.itertext())


def test_baidu_resume_polls_receipt_without_resubmission(tmp_path, monkeypatch):
    monkeypatch.setenv('TEXTFERRY_MOCK_OCR', '0')
    get_textferry_settings.cache_clear()
    receipt = tmp_path / 'receipt.json'
    source = text_pdf(tmp_path / 'test.pdf')
    posts = []
    polls = []

    async def post(*args, **kwargs):
        posts.append(1)
        return httpx.Response(200, json={'data': {'jobId': 'synthetic-job'}})

    async def get(client, url, **kwargs):
        polls.append(url)
        assert json.loads(receipt.read_text())['job_id'] == 'synthetic-job'
        if len(polls) == 1:
            raise OcrClientError('poll interrupted', provider='baidu_async', stage='poll', error_type='Timeout', retryable=True)
        if url.endswith('/synthetic-job'):
            return httpx.Response(200, json={'data': {'state': 'done', 'resultUrl': {'jsonUrl': 'https://example.test/result'}}})
        return httpx.Response(200, text='unused')

    monkeypatch.setattr('textferry.ocr_client._baidu_post', post)
    monkeypatch.setattr('textferry.ocr_client._baidu_get', get)
    monkeypatch.setattr('textferry.ocr_client._parse_baidu_jsonl_page_texts', lambda text: {1: 'recovered text'})
    provider = BaiduAsyncProvider()
    options = dict(pdf_path=source, model_option=get_model_registry().options['paddleocr_vl'], api_key='synthetic', expected_pages=1, receipt_path=receipt)
    with pytest.raises(OcrClientError):
        asyncio.run(provider.ocr_pdf_pages_baidu_async(**options))
    assert asyncio.run(provider.ocr_pdf_pages_baidu_async(**options)) == {1: 'recovered text'}
    assert posts == [1]


def test_unknown_baidu_submission_is_not_reposted(tmp_path, monkeypatch):
    monkeypatch.setenv('TEXTFERRY_MOCK_OCR', '0')
    get_textferry_settings.cache_clear()
    calls = []
    async def ambiguous(*args, **kwargs):
        calls.append(1)
        raise OcrClientError('unknown submit result', provider='baidu_async', stage='submit', error_type='ReadTimeout', retryable=True)
    monkeypatch.setattr('textferry.ocr_client._baidu_post', ambiguous)
    provider = BaiduAsyncProvider()
    options = dict(pdf_path=text_pdf(tmp_path / 'test.pdf'), model_option=get_model_registry().options['paddleocr_vl'], api_key='synthetic', expected_pages=1, receipt_path=tmp_path / 'receipt.json')
    for _ in range(2):
        with pytest.raises(OcrClientError):
            asyncio.run(provider.ocr_pdf_pages_baidu_async(**options))
    assert calls == [1]


def test_basic_word_export_does_not_claim_to_include_direct_markdown_edits(tmp_path):
    bundle = tmp_path / 'bundle'
    workflow.convert(text_pdf(tmp_path / 'source.pdf'), bundle, output_format='word')
    candidate = bundle / '基础稿.docx'
    prior = candidate.read_bytes()
    (bundle / 'machine.md').write_text('User text has changed substantially.')
    result = workflow.resume(bundle)
    assert result['machine_user_modified']
    assert candidate.read_bytes() == prior


def test_text_pdf_keeps_original_figures_in_markdown_and_word(tmp_path):
    source = tmp_path / 'illustrated.pdf'
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 80), False)
    pix.clear_with(90)
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), 'Body and figure caption.')
        page.insert_image(fitz.Rect(40, 100, 240, 260), stream=pix.tobytes('png'))
        doc.save(source)
    bundle = tmp_path / 'bundle'
    result = workflow.convert(source, bundle, output_format='both')
    assert result['status'] == 'machine_ready'
    assert '![' in (bundle / 'machine.md').read_text()
    assert list((bundle / 'assets').rglob('*.png'))
    with ZipFile(bundle / '基础稿.docx') as z:
        assert len([name for name in z.namelist() if name.startswith('word/media/')]) == 1


@pytest.mark.parametrize('location', ['body', 'note'])
def test_pdf_referenced_figure_change_invalidates_review(tmp_path, location):
    from textferry import handwash
    bundle = tmp_path / 'bundle'
    workflow.convert(text_pdf(tmp_path / 'source.pdf'), bundle, mode='review')
    assets = bundle / 'assets'; assets.mkdir()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 20), False)
    pix.clear_with(200); pix.save(assets / 'figure.png')
    body = 'Authored illustration.\n\n![figure](assets/figure.png)\n' if location == 'body' else 'Authored note.[^n]\n\n[^n]: Illustration note.\n\n    ![figure](assets/figure.png)\n'
    (bundle / 'review/pages/0001.md').write_text(body)
    handwash.prepare_handwash(bundle)
    workflow.review_page(bundle, 1, checks='body,order,notes,structure', reviewer='test', note='Synthetic mechanism test.', issues=[])
    handwash.export_handwash(bundle, reviewer='test', note='Synthetic assembly.')
    pix.clear_with(0); pix.save(assets / 'figure.png')
    assert handwash.handwash_status(bundle)['current'] is False
    root, manifest = workflow.read_manifest(bundle)
    result = workflow.collect_report(root, manifest, workflow.engine(root, manifest))
    assert result['stale_review_pages'] == [1]


def test_page_selection_and_local_format_upgrade_reuse_content(tmp_path, monkeypatch):
    from textferry.service import TextFerryService
    source = tmp_path / 'pages.pdf'
    with fitz.open() as doc:
        for n in range(1, 4):
            doc.new_page().insert_text((40, 60), f'Unique page {n}.')
        doc.save(source)
    bundle = tmp_path / 'bundle'
    result = workflow.convert(source, bundle, pages='2-3')
    assert result['source_page_map'] == [2, 3]
    assert result['processed_pages'] == 2
    assert 'Unique page 1.' not in (bundle / 'machine.md').read_text()
    assert 'PDF页:2' in (bundle / 'machine.md').read_text()
    monkeypatch.setattr(TextFerryService, 'run_job', lambda *a, **k: pytest.fail('Local follow-ups must not run OCR'))
    workflow.change_output(bundle, output_format='word')
    assert (bundle / '基础稿.docx').is_file()
    workflow.upgrade_review(bundle)
    assert (bundle / 'review/pages/0001.png').is_file()
    assert json.loads((bundle / 'manifest.json').read_text())['mode'] == 'review'


def test_discontinuous_pdf_pages_cannot_be_joined(tmp_path):
    from textferry import handwash
    source = tmp_path / 'pages.pdf'
    with fitz.open() as doc:
        for n in range(3):
            doc.new_page().insert_text((40, 60), f'Source {n}.')
        doc.save(source)
    bundle = tmp_path / 'bundle'
    workflow.convert(source, bundle, mode='review', pages='1,3')
    handwash.prepare_handwash(bundle)
    path = bundle / 'handwash/layout.json'
    layout = json.loads(path.read_text()); layout['pages'][1]['boundary'] = 'join'
    workflow.write_json(path, layout)
    for n in (1, 2):
        workflow.review_page(bundle, n, checks='body,order,notes,structure', reviewer='test', note='Synthetic source.', issues=[])
    with pytest.raises(ValueError, match='不连续'):
        handwash.export_handwash(bundle, reviewer='test', note='Must not join gaps.')


def test_literal_images_in_code_do_not_require_assets(tmp_path):
    bundle = tmp_path / 'bundle'
    workflow.convert(text_pdf(tmp_path / 'source.pdf'), bundle, mode='review')
    (bundle / 'review/pages/0001.md').write_text('Literal `![x](missing.png)`.\n\n```markdown\n![x](another-missing.png)\n```\n')
    result = workflow.review_page(bundle, 1, checks='body,order,notes,structure', reviewer='test', note='Code is material.', issues=[])
    assert result['reviewed_pages'] == [1]


def test_explicit_word_addition_uses_user_edited_basic_text(tmp_path):
    bundle = tmp_path / 'bundle'
    workflow.convert(text_pdf(tmp_path / 'source.pdf'), bundle)
    target = bundle / 'machine.md'
    target.write_text(target.read_text() + '\nExplicit user revision.\n')
    result = workflow.change_output(bundle, output_format='word')
    assert result['machine_user_modified']
    with ZipFile(bundle / '基础稿.docx') as z:
        root = ET.fromstring(z.read('word/document.xml'))
    assert 'Explicit user revision.' in ''.join(root.itertext())


def test_engine_uses_the_same_effective_baidu_token_as_setup(tmp_path, monkeypatch):
    from textferry.credentials import effective_credential
    from textferry.service import TextFerryService
    from textferry.config import get_textferry_settings
    from dataclasses import replace
    monkeypatch.delenv('TEXTFERRY_MOCK_OCR', raising=False)
    monkeypatch.setenv('PADDLEOCR_ACCESS_TOKEN', 'preferred-user-token')
    monkeypatch.setenv('BAIDU_API_KEY', 'another-token')
    settings = replace(get_textferry_settings(), data_dir=tmp_path / 'jobs')
    service = TextFerryService(settings=settings)
    assert service._resolve_api_key('', 'baidu_async') == effective_credential('baidu_async')[0]
    assert service._resolve_api_key('explicit-token', 'baidu_async') == 'explicit-token'
