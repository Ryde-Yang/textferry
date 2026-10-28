"""队列满/限流分类、traceId保留与无损暂停回归结果。"""
import asyncio

import httpx
import pytest

from textferry.config import get_textferry_settings, get_model_registry
from textferry.ocr_client import BaiduAsyncProvider, OcrClientError
from textferry.service import TextFerryService
from textferry.types import PageMode


@pytest.mark.parametrize('status,code,kind', [(400, 10010, 'QueueFull'), (429, 12002, 'RateLimited')])
def test_baidu_submit_classifies_backpressure(textferry_env, make_pdf, monkeypatch, status, code, kind):
    monkeypatch.delenv('TEXTFERRY_MOCK_OCR')
    get_textferry_settings.cache_clear()
    get_model_registry.cache_clear()

    async def response(*args, **kwargs):
        return httpx.Response(status, json={'code': code, 'msg': 'busy', 'traceId': 'test-trace'})

    monkeypatch.setattr('textferry.ocr_client._baidu_post', response)
    with pytest.raises(OcrClientError) as caught:
        asyncio.run(BaiduAsyncProvider().ocr_pdf_pages_baidu_async(
            pdf_path=make_pdf(textferry_env / 'probe.pdf', 1),
            model_option=get_model_registry().options['paddleocr_vl'], api_key='test', expected_pages=1))
    assert caught.value.error_type == kind
    assert caught.value.retryable is True
    assert caught.value.provider_code == code
    assert caught.value.trace_id == 'test-trace'


@pytest.mark.parametrize('kind', ['QueueFull', 'RateLimited'])
def test_baidu_backpressure_pauses_without_splitting_or_failing_pages(textferry_env, make_pdf, monkeypatch, kind):
    monkeypatch.setenv('TEXTFERRY_MAX_RETRIES', '3')
    monkeypatch.setenv('TEXTFERRY_BAIDU_MIN_SPLIT_PAGES', '2')
    monkeypatch.setenv('TEXTFERRY_BAIDU_BATCH_SIZE', '4')
    get_textferry_settings.cache_clear()
    get_model_registry.cache_clear()
    service = TextFerryService()
    job = service.create_job_from_pdf_path(pdf_path=make_pdf(textferry_env / 'book.pdf', 10), page_mode=PageMode.PDF)
    calls = []

    async def busy(**kwargs):
        calls.append(kwargs['expected_pages'])
        raise OcrClientError('baidu submit busy', provider='baidu_async', stage='submit', error_type=kind, retryable=True)

    service._baidu.ocr_pdf_pages_baidu_async = busy
    result = service.run_job(job.job_id, provider='baidu_async')
    assert calls == [4]
    assert result['status'] == 'CREATED'
    assert result['progress']['processedPages'] == 0
    assert result['progress']['failedPagesCount'] == 0
    assert all(p['status'] == 'PENDING' for p in service.store.list_pages(job.job_id))
    assert result['artifacts']['final'] is None
    assert 'baidu_split' not in [d['type'] for d in service.store.read_diagnostics(job.job_id)]
    assert 'job_provider_unavailable' in [e['event'] for e in service.store.read_events(job.job_id)]


def test_baidu_v16_is_retained_and_sent_to_provider(textferry_env, make_pdf):
    get_textferry_settings.cache_clear()
    get_model_registry.cache_clear()
    service = TextFerryService()
    job = service.create_job_from_pdf_path(pdf_path=make_pdf(textferry_env / 'v16.pdf', 1), page_mode=PageMode.PDF, model_option='paddleocr_vl_16')
    seen = []

    async def recognize(*, model_option, **kwargs):
        seen.append(model_option.model_id)
        return {1: '這是一段模型版本驗證用的正文。'}

    service._baidu.ocr_pdf_pages_baidu_async = recognize
    result = service.run_job(job.job_id, provider='baidu_async')
    assert result['modelOption'] == 'paddleocr_vl_16'
    assert seen == ['PaddleOCR-VL-1.6']
    assert result['status'] == 'FINAL_READY'


def test_real_client_receipt_survives_regrouped_partial_resume(textferry_env, make_pdf, monkeypatch):
    import json
    from dataclasses import replace
    monkeypatch.setenv('TEXTFERRY_MOCK_OCR', '0')
    get_textferry_settings.cache_clear()
    settings = replace(get_textferry_settings(), batch_size=4, baidu_batch_size=4, enable_reader_docx=False)
    service = TextFerryService(settings=settings)
    job = service.create_job_from_pdf_path(pdf_path=make_pdf(textferry_env / 'book.pdf', 4), page_mode=PageMode.PDF)
    posts, polls = [], []
    async def post(*args, **kwargs):
        posts.append(1)
        return httpx.Response(200, json={'data': {'jobId': 'accepted-job'}})
    async def get(client, url, **kwargs):
        polls.append(url)
        if len(polls) == 1:
            raise OcrClientError('poll interruption', provider='baidu_async', stage='poll', error_type='Timeout', retryable=True)
        if url.endswith('/accepted-job'):
            return httpx.Response(200, json={'data': {'state': 'done', 'resultUrl': {'jsonUrl': 'https://example.test/result'}}})
        return httpx.Response(200, text='synthetic result')
    monkeypatch.setattr('textferry.ocr_client._baidu_post', post)
    monkeypatch.setattr('textferry.ocr_client._baidu_get', get)
    monkeypatch.setattr('textferry.ocr_client._parse_baidu_jsonl_page_texts', lambda _: {n: f'原任务第 {n} 页的自制中文正文，不能重复提交识别。' for n in range(1, 5)})
    first = service.run_job(job.job_id, provider='baidu_async')
    assert first['status'] == 'CREATED'
    assert all(p['status'] == 'PENDING' for p in service.store.list_pages(job.job_id))
    receipt = next(service.store.job_dir(job.job_id).glob('remote-requests/*/receipt.json'))
    assert json.loads(receipt.read_text())['job_id'] == 'accepted-job'
    service.settings = replace(settings, batch_size=2, baidu_batch_size=2)
    final = service.run_job(job.job_id, provider='baidu_async')
    assert final['status'] == 'FINAL_READY'
    assert posts == [1]
    assert final['progress']['processedPages'] == 4


def test_ambiguous_submit_never_splits_or_reposts(textferry_env, make_pdf, monkeypatch):
    monkeypatch.setenv('TEXTFERRY_MOCK_OCR', '0')
    monkeypatch.setenv('TEXTFERRY_BAIDU_MIN_SPLIT_PAGES', '2')
    get_textferry_settings.cache_clear()
    service = TextFerryService()
    job = service.create_job_from_pdf_path(pdf_path=make_pdf(textferry_env / 'book.pdf', 4), page_mode=PageMode.PDF)
    posts = []
    async def post(*args, **kwargs):
        posts.append(1)
        raise OcrClientError('submit read timeout', provider='baidu_async', stage='submit', error_type='ReadTimeout', retryable=True)
    monkeypatch.setattr('textferry.ocr_client._baidu_post', post)
    for _ in range(2):
        result = service.run_job(job.job_id, provider='baidu_async')
        assert result['status'] == 'CREATED'
        assert result['progress']['failedPagesCount'] == 0
    assert posts == [1]
    assert 'baidu_split' not in [d['type'] for d in service.store.read_diagnostics(job.job_id)]


def test_baidu_reports_wait_and_download_stages(textferry_env, make_pdf, monkeypatch, capsys):
    monkeypatch.setenv('TEXTFERRY_MOCK_OCR', '0')
    get_textferry_settings.cache_clear()
    async def post(*args, **kwargs):
        return httpx.Response(200, json={'data': {'jobId': 'synthetic-progress'}})
    async def get(client, url, **kwargs):
        if url.endswith('/synthetic-progress'):
            return httpx.Response(200, json={'data': {'state': 'done', 'resultUrl': {'jsonUrl': 'https://example.test/result'}}})
        return httpx.Response(200, text='synthetic')
    monkeypatch.setattr('textferry.ocr_client._baidu_post', post)
    monkeypatch.setattr('textferry.ocr_client._baidu_get', get)
    monkeypatch.setattr('textferry.ocr_client._parse_baidu_jsonl_page_texts', lambda _: {1: '自制正文'})
    asyncio.run(BaiduAsyncProvider().ocr_pdf_pages_baidu_async(pdf_path=make_pdf(textferry_env / 'p.pdf', 1), model_option=get_model_registry().options['paddleocr_vl'], api_key='synthetic', expected_pages=1))
    output = capsys.readouterr().err
    assert 'ocr_wait' in output and 'ocr_download' in output
    assert 'synthetic-progress' not in output and 'Bearer' not in output
