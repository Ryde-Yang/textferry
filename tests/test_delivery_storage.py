"""User delivery and reconstructible caches, using authored local material."""
import json
import shutil
from pathlib import Path

import pymupdf as fitz
import pytest

from textferry import credentials, handwash, workflow


def source(path, pages=2):
    with fitz.open() as doc:
        for n in range(pages):
            doc.new_page().insert_text((50, 70), f'Authored page {n + 1}.')
        doc.save(path)
    return path


def reviewed(tmp_path):
    bundle = tmp_path / '成果' / '.textferry'
    workflow.convert(source(tmp_path / 'source.pdf'), bundle, mode='review', pages='1-2')
    handwash.prepare_handwash(bundle)
    layout = json.loads((bundle / 'handwash/layout.json').read_text())
    layout['title'] = '自制材料'
    layout['pages'][1]['boundary'] = 'separate'
    workflow.write_json(bundle / 'handwash/layout.json', layout)
    for n in (1, 2):
        workflow.review_page(bundle, n, checks='body,order,notes,structure', reviewer='test', note='Compared authored source.', issues=[])
    handwash.export_handwash(bundle, reviewer='test', note='Verified source and boundaries.')
    return bundle


def test_setup_reports_effective_source_without_reading_broken_unused_config(tmp_path, monkeypatch):
    monkeypatch.setenv('TEXTFERRY_CONFIG_DIR', str(tmp_path))
    monkeypatch.setenv('PADDLEOCR_ACCESS_TOKEN', 'synthetic-env-private-token')
    monkeypatch.setenv('BAIDU_API_KEY', 'synthetic-older-private-token')
    (tmp_path / 'credentials.json').write_text('{broken unused file')
    result = credentials.onboarding()
    assert result['credential_source'] == 'environment:PADDLEOCR_ACCESS_TOKEN'
    assert result['configured'] is True
    assert 'private-token' not in json.dumps(result)
    assert result['execution_config_dir'] == str(tmp_path.resolve())


def test_selected_pages_keep_one_portable_pdf_and_original_page_mapping(tmp_path):
    bundle = tmp_path / 'bundle'
    workflow.convert(source(tmp_path / 'source.pdf', 3), bundle, pages='2-3')
    assert len(list(bundle.rglob('*.pdf'))) == 1
    root, manifest = workflow.read_manifest(bundle)
    assert manifest['source_page_map'] == [2, 3]
    assert manifest['original_source_sha256']
    text = (bundle / 'machine.md').read_text()
    assert 'PDF页:2' in text and '#page=1' in text
    assert 'Authored page 1.' not in text
    assert (root / manifest['source_pdf']).exists()


def test_compact_keeps_review_current_and_word_addition_offline(tmp_path, monkeypatch):
    bundle = reviewed(tmp_path)
    from textferry import housekeeping
    job = next((bundle / '_work/jobs').iterdir())
    for rel in ['batches/batch_0001.pdf', 'remote-requests/test/input.pdf']:
        p = job / rel; p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job / 'input.pdf', p)
    receipt = job / 'remote-requests/test/receipt.json'
    receipt.write_text('{"job_id":"completed-authored-test"}')
    (bundle / 'assets').mkdir()
    (bundle / 'assets/keep.txt').write_text('user-owned resource')
    before = sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file())
    preview = housekeeping.compact(bundle)
    assert preview['status'] == 'cleanup_preview'
    assert len(list(bundle.rglob('*.pdf'))) == 3
    result = housekeeping.compact(bundle, apply=True)
    assert result['removed_bytes'] > 0 and result['removed_bytes'] < before
    assert len(list(bundle.rglob('*.pdf'))) == 1
    assert not list(bundle.glob('review/pages/*.png'))
    assert receipt.exists() and (bundle / 'assets/keep.txt').exists()
    assert handwash.handwash_status(bundle)['current']
    root, manifest = workflow.read_manifest(bundle)
    workflow.export_bundle(root, manifest, workflow.engine(root, manifest))
    assert not list(bundle.glob('review/pages/*.png'))
    monkeypatch.setattr('textferry.service.TextFerryService.run_job', lambda *a, **kw: pytest.fail('must reuse text'))
    workflow.change_output(bundle, output_format='both')
    assert (bundle / '整理稿.docx').exists()
    assert handwash.handwash_status(bundle)['current']
    image = housekeeping.page_image(bundle, page=1)
    assert Path(image['image']).exists()
    assert handwash.handwash_status(bundle)['current']


def test_cleanup_rejects_unfinished_or_changed_review_without_deleting(tmp_path):
    from textferry import housekeeping
    bundle = tmp_path / 'bundle'
    workflow.convert(source(tmp_path / 'source.pdf'), bundle, mode='review')
    images = list(bundle.glob('review/pages/*.png'))
    with pytest.raises(ValueError, match='完成|成稿|核查'):
        housekeeping.compact(bundle, apply=True)
    assert all(p.exists() for p in images)
    other = tmp_path / 'other'; other.mkdir()
    ready = reviewed(other)
    png = ready / 'review/pages/0001.png'
    png.write_bytes(png.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='变化|核查|当前'):
        housekeeping.compact(ready, apply=True)
    assert png.exists()


def test_delivery_has_three_readable_files_and_preserves_user_changes(tmp_path):
    from textferry import delivery
    bundle = reviewed(tmp_path)
    result = delivery.deliver(bundle)
    output = bundle.parent
    assert set(p.name for p in output.iterdir() if p.is_file()) == {'整理稿.md', '机器底稿.md', '整理说明.md'}
    assert result['files']['整理稿'] == str(output / '整理稿.md')
    assert '[整理稿](<' + str(output / '整理稿.md') + '>)' in result['delivery_message']
    text = (output / '机器底稿.md').read_text()
    assert '.textferry/_work/' in text
    assert 'report.json' not in result['files']
    (output / '整理稿.md').write_text('User revision must remain.')
    with pytest.raises(ValueError, match='修改|修订'):
        delivery.deliver(bundle)
    with pytest.raises(ValueError, match='修改|修订'):
        workflow.change_output(bundle, output_format='both')
    assert (output / '整理稿.md').read_text() == 'User revision must remain.'


def test_compacted_delivery_can_move_and_add_word(tmp_path, monkeypatch):
    from textferry import delivery, housekeeping
    bundle = reviewed(tmp_path)
    delivery.deliver(bundle)
    housekeeping.compact(bundle, apply=True)
    moved = tmp_path / 'moved'
    shutil.move(str(bundle.parent), str(moved))
    monkeypatch.setattr('textferry.service.TextFerryService.run_job', lambda *a, **kw: pytest.fail('offline followup'))
    workflow.change_output(moved, output_format='both')
    result = delivery.deliver(moved)
    assert (moved / '整理稿.docx').exists()
    assert result['files']['整理稿'] == str(moved / '整理稿.md')
    assert handwash.handwash_status(moved / '.textferry')['current']


def test_delivery_relocates_note_images_but_preserves_literal_code(tmp_path):
    from textferry.delivery import relocate_markdown
    bundle = tmp_path / '.textferry'; bundle.mkdir()
    text = ('Body ![图](assets/a.png). `![例子](assets/a.png)`\n\n'
            '[^1]: 原注\n\n    ![注图](assets/b.png)\n\n'
            '```md\n![例子](assets/c.png)\n```\n')
    result = relocate_markdown(text, bundle, tmp_path)
    assert '![注图](.textferry/assets/b.png)' in result
    assert '`![例子](assets/a.png)`' in result
    assert '![例子](assets/c.png)' in result


def test_cleanup_source_change_invalidates_archived_evidence(tmp_path):
    from textferry import housekeeping
    bundle = reviewed(tmp_path)
    housekeeping.compact(bundle, apply=True)
    root, manifest = workflow.read_manifest(bundle)
    path = root / manifest['source_pdf']
    path.write_bytes(path.read_bytes() + b'changed source')
    assert not handwash.handwash_status(bundle)['current']


def test_compact_does_not_remove_external_symlink_target(tmp_path):
    from textferry import housekeeping
    bundle = reviewed(tmp_path)
    external = tmp_path / 'keep.pdf'; source(external)
    target = next((bundle / '_work/jobs').iterdir()) / 'batches/batch_external.pdf'
    target.parent.mkdir(exist_ok=True); target.symlink_to(external)
    with pytest.raises(ValueError, match='链接|任务外'):
        housekeeping.compact(bundle, apply=True)
    assert external.exists()


def test_task_status_is_small_and_reports_review_progress(tmp_path):
    bundle = reviewed(tmp_path)
    status = workflow.task_status(bundle)
    assert status['processed'] == status['total'] == 2
    assert status['reviewed'] == 2
    assert status['stage'] == 'ready'
    assert 'review_records' not in status and 'source_sha256' not in status


def test_utf8_note_file_preserves_formula_quotes_without_shell_expansion(tmp_path, capsys):
    bundle = tmp_path / 'bundle'
    workflow.convert(source(tmp_path / 'source.pdf', 1), bundle, mode='review')
    note = tmp_path / 'note.txt'
    text = '已核对 $x + y = 3$、`原注`与“引文”；不改字。'
    note.write_text(text, encoding='utf-8')
    code = workflow.main(['review-page', str(bundle), '--page', '1', '--checks', 'body,order,notes,structure', '--reviewer', 'test', '--note-file', str(note)])
    assert code == 0
    ledger = json.loads((bundle / 'review/ledger.json').read_text())
    assert ledger['entries'][-1]['note'] == text


def test_delivery_receipt_uses_actual_assembled_scope(tmp_path):
    from textferry import delivery
    bundle = reviewed(tmp_path)
    path = bundle / 'handwash/layout.json'
    layout = json.loads(path.read_text()); layout['pages'] = layout['pages'][:1]
    workflow.write_json(path, layout)
    handwash.export_handwash(bundle, reviewer='test', note='Only first authored page selected.')
    result = delivery.deliver(bundle)
    assert '覆盖 1 页' in result['delivery_message']
    assert list(result['files'])[0] == '整理稿'
