import json
from pathlib import Path

import pymupdf as fitz
import pytest

from textferry import workflow


@pytest.fixture
def bundle(tmp_path):
    pdf = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        for text in ('A page one.', 'B page two.'):
            doc.new_page().insert_text((72, 72), text)
        doc.save(pdf)
    target = tmp_path / 'bundle'
    workflow.convert(pdf, target, mode='review')
    return target


def api():
    from textferry import handwash
    return handwash


def ready(bundle, *, issue=False):
    handwash = api()
    handwash.prepare_handwash(bundle)
    (bundle / 'review/pages/0001.md').write_text('## 第一节\n\n作者的论证延续到', encoding='utf-8')
    (bundle / 'review/pages/0002.md').write_text('下一页。[^1] 引用书目。[^ref-1]\n\n[^1]: 原书脚注。\n    注释续段。\n\n[^ref-1]: 原书参考文献。\n', encoding='utf-8')
    layout_path = bundle / 'handwash/layout.json'
    layout = json.loads(layout_path.read_text())
    layout['title'] = '材料整理示例'
    layout['pages'][1]['boundary'] = 'join'
    layout['pages'][1]['printed_page'] = '二'
    layout_path.write_text(json.dumps(layout, ensure_ascii=False), encoding='utf-8')
    for number in (1, 2):
        workflow.review_page(bundle, number, checks='body,order,notes,structure', reviewer='Codex', note='对照页图保留正文、引文、注释与层次。', issues=['疑字待核'] if issue and number == 2 else [])
    return handwash


def export(bundle, **kwargs):
    return api().export_handwash(bundle, reviewer='Codex', note='复核跨页接续、章节边界及全篇注释配对。', **kwargs)


def test_review_conversion_does_not_masquerade_as_handwashed(bundle):
    assert not (bundle / 'handwashed.md').exists()
    handwash = api()
    handwash.prepare_handwash(bundle)
    with pytest.raises(ValueError, match='核查'):
        export(bundle)


def test_handwash_two_outputs_hidden_markers_and_source_map(bundle):
    machine = (bundle / 'machine.md').read_bytes()
    ready(bundle)
    report = export(bundle)
    text = (bundle / 'handwashed.md').read_text()
    assert '作者的论证延续到下一页。[^1]' in text
    assert '## 注释' in text and '## 参考文献' in text
    assert text.index('## 注释') > text.index('引用书目')
    assert '注释续段。' in text
    assert 'PDF 第' not in text and 'SRC:' not in text
    assert (bundle / 'machine.md').read_bytes() == machine
    sources = json.loads((bundle / 'handwashed.sources.json').read_text())
    assert sources['covered_pages'] == [1, 2]
    assert sources['offset_unit'] == 'unicode_codepoints'
    for row in sources['pages']:
        assert text[row['start']:row['end']]
    assert sources['notes']['1']['pdf_page'] == 2
    assert sources['stage'] == '已手洗' and sources['academic_clearance'] is False
    assert report['status'] == 'handwashed'
    bundle_path, manifest = workflow.read_manifest(bundle)
    report = workflow.export_bundle(bundle_path, manifest, workflow.engine(bundle_path, manifest))
    assert report['handwash']['current'] is True
    assert 'handwashed.md' in report['artifacts']


def test_show_page_numbers_keeps_mapping_and_same_source_draft(bundle):
    ready(bundle)
    export(bundle, page_numbers='show')
    text = (bundle / 'handwashed.md').read_text()
    assert '〔PDF 第 1 页〕' in text
    assert '〔PDF 第 2 页；书页 二〕' in text
    export(bundle, page_numbers='hide')
    assert 'PDF 第' not in (bundle / 'handwashed.md').read_text()


def test_export_normalizes_spaced_math_without_modifying_review_evidence(bundle):
    ready(bundle)
    draft=bundle/'review/pages/0001.md'
    original='变量 $ x_{1} $。'
    draft.write_text(original)
    workflow.review_page(bundle,1,checks='body,order,notes,structure',reviewer='Codex',note='自制公式样本。',issues=[])
    # Keep page two's paired notes and source attribution intact.
    export(bundle)
    text=(bundle/'handwashed.md').read_text()
    assert '$x_{1}$' in text
    assert draft.read_text()==original
    mapping=json.loads((bundle/'handwashed.sources.json').read_text())
    page=mapping['pages'][0]
    assert text[page['start']:page['end']]=='变量 $x_{1}$。'


def test_unresolved_boundary_blocks_final_export(bundle):
    ready(bundle)
    path = bundle / 'handwash/layout.json'
    data = json.loads(path.read_text())
    data['pages'][1]['boundary'] = 'unchecked'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='跨页'):
        export(bundle)


def test_partial_scope_is_explicit_and_never_joins_nonconsecutive_pages(bundle):
    ready(bundle)
    path = bundle / 'handwash/layout.json'
    data = json.loads(path.read_text())
    data['pages'] = data['pages'][1:]
    data['pages'][0]['boundary'] = 'separate'
    path.write_text(json.dumps(data))
    export(bundle)
    source = json.loads((bundle / 'handwashed.sources.json').read_text())
    assert source['covered_pages'] == [2] and source['coverage'] == 'selected_pages'
    assert source['excluded_pages'] == [1]


@pytest.mark.parametrize('bad', ['正文。[^missing]', '正文。\n\n[^orphan]: 无对应正文。', '正文。[^n]\n\n[^n]: 注一。\n\n[^n]: 注二。'])
def test_broken_footnotes_block_export(bundle, bad):
    ready(bundle)
    (bundle / 'review/pages/0002.md').write_text(bad)
    workflow.review_page(bundle, 2, checks='body,order,notes,structure', reviewer='Codex', note='测试有问题的注释。', issues=[])
    with pytest.raises(ValueError, match='注释'):
        export(bundle)


def test_unresolved_content_is_explicitly_labeled(bundle):
    ready(bundle, issue=True)
    with pytest.raises(ValueError, match='待核'):
        export(bundle)
    result = export(bundle, allow_unresolved=True)
    assert result['status'] == 'handwashed_with_issues'
    assert 'handwashed.sources.json' not in (bundle / 'handwashed.md').read_text()
    assert '仍有待核问题' not in (bundle / 'handwashed.md').read_text()
    readable = (bundle / '整理说明.md').read_text()
    assert '疑字待核' in readable and '二' in readable
    assert '.json' not in readable and 'handwashed_with_issues' not in readable
    assert json.loads((bundle / 'handwashed.sources.json').read_text())['unresolved_pages'] == [2]


def test_edits_invalidate_export_and_do_not_overwrite_user_final(bundle):
    handwash = ready(bundle)
    export(bundle)
    assert handwash.handwash_status(bundle)['current'] is True
    final = bundle / 'handwashed.md'
    final.write_text(final.read_text() + '\n用户自己写的内容。\n')
    assert handwash.handwash_status(bundle)['current'] is False
    with pytest.raises(ValueError, match='成稿.*修改'):
        export(bundle)
    assert '用户自己写的内容' in final.read_text()


def test_source_work_and_layout_changes_invalidate_handwash(bundle):
    handwash = ready(bundle)
    export(bundle)
    (bundle / 'review/pages/0001.md').write_text('又一次修改。')
    assert handwash.handwash_status(bundle)['current'] is False
    with pytest.raises(ValueError, match='核查'):
        export(bundle)


def test_prepare_preserves_existing_layout(bundle):
    handwash = ready(bundle)
    layout = (bundle / 'handwash/layout.json').read_bytes()
    handwash.prepare_handwash(bundle)
    assert (bundle / 'handwash/layout.json').read_bytes() == layout


def test_share_preserves_both_primary_texts_and_checks_all_payloads(bundle, tmp_path):
    from textferry import sharing
    from test_sharing import catalog_for
    ready(bundle)
    export(bundle)
    rights = tmp_path / 'rights.json'
    rights.write_text(json.dumps({'title': '自制材料', 'edition': '自制版本', 'scope_description': '两页测试材料', 'contribution_license': 'CC0-1.0', 'source_rights': {'basis': 'permission', 'jurisdiction': 'CN', 'statement': '自制测试材料授权', 'evidence': ['测试授权']}}))
    target = tmp_path / 'candidate'
    sharing.prepare_share(bundle, rights, target)
    assert (target / 'handwashed.md').read_bytes() == (bundle / 'handwashed.md').read_bytes()
    raw = (target / 'machine.md').read_text()
    assert 'A page one.' in raw and '作者的论证延续到' not in raw
    assert str(bundle) not in '\n'.join(p.read_text() for p in target.iterdir())
    repository = 'example/textferry'
    sharing.record_share_decision(bundle, target, 'yes', repository=repository)
    index, metadata, public = catalog_for(target, tmp_path)
    downloaded = tmp_path / 'downloaded'
    sharing.download_shared(index, metadata['id'], downloaded)
    assert (downloaded / 'handwashed.md').read_bytes() == (bundle / 'handwashed.md').read_bytes()
    assert (downloaded / 'machine.md').read_text() == raw
    reused = tmp_path / 'reused'
    source = json.loads((bundle / 'manifest.json').read_text())
    report = sharing.reuse_shared(bundle / source['source_pdf'], index, metadata['id'], reused, mode='review')
    assert report['reviewed_pages'] == []
    assert (reused / 'shared/handwashed.md').exists()
    assert 'A page one.' in (reused / 'machine.md').read_text()
    assert '作者的论证延续到' in (reused / 'review/pages/0001.md').read_text()
    (target / 'handwashed.md').write_text('换了一版')
    assert not sharing.share_status(bundle, target, repository=repository)['consented']
    (public / 'machine.md').write_text('损坏了')
    with pytest.raises(ValueError, match='校验'):
        sharing.download_shared(index, metadata['id'], tmp_path / 'bad-download')


def test_stale_handwash_cannot_silently_share_old_final(bundle, tmp_path):
    from textferry.sharing import prepare_share
    ready(bundle)
    export(bundle)
    (bundle / 'review/pages/0001.md').write_text('最新改动')
    with pytest.raises(ValueError, match='手洗.*失效'):
        prepare_share(bundle, tmp_path / 'not-read.json', tmp_path / 'candidate')


def test_footnotes_do_not_leave_empty_duplicate_endmatter_headings(bundle):
    ready(bundle)
    (bundle / 'review/pages/0002.md').write_text('下一页。[^1] 引用。[^ref-1]\n\n## 注释\n\n[^1]: 原注。\n\n## 参考文献\n\n[^ref-1]: 原书目。')
    workflow.review_page(bundle, 2, checks='body,order,notes,structure', reviewer='Codex', note='已核两套注释及章节层次。', issues=[])
    export(bundle)
    text = (bundle / 'handwashed.md').read_text()
    assert text.count('## 注释') == 1
    assert text.count('## 参考文献') == 1


def test_word_can_be_selected_and_preserves_real_math(bundle):
    from zipfile import ZipFile
    from lxml import etree
    ready(bundle)
    page = bundle / 'review/pages/0002.md'
    page.write_text(r"""下一页。公式 $x^2+1$。[^n]

$$
\begin{pmatrix}1 & 2 \\ 3 & 4\end{pmatrix}
$$

| 项目 | 数值 |
|---|---|
| 示例 | 1 |

[^n]: 原书注释。
""")
    workflow.review_page(bundle, 2, checks='body,order,notes,structure', reviewer='Codex', note='自制公式、矩阵、表格和脚注格式测试。', issues=[])
    result=export(bundle, output_format='word')
    target=bundle/'整理稿.docx'
    assert target.exists()
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
    with ZipFile(target) as z:
        body=etree.fromstring(z.read('word/document.xml'))
        assert len(body.xpath('//m:oMath',namespaces=ns))==2
        assert len(body.xpath('//w:tbl',namespaces=ns))==1
        assert 'word/footnotes.xml' in z.namelist()
        assert len(body.xpath('//w:footnoteReference',namespaces=ns))==1
        assert 'handwashed.sources.json' not in ''.join(body.itertext())
    assert result['current']


def test_user_edited_word_file_is_preserved(bundle):
    handwash=ready(bundle)
    export(bundle, output_format='word')
    path=bundle/'整理稿.docx'
    path.write_bytes(b'user-owned edited document')
    assert not handwash.handwash_status(bundle)['current']
    with pytest.raises(ValueError,match='Word|成稿'):
        export(bundle, output_format='word')
    assert path.read_bytes()==b'user-owned edited document'


def test_markdown_remains_the_default_format(bundle):
    ready(bundle)
    export(bundle)
    assert (bundle/'handwashed.md').exists()
    assert not (bundle/'整理稿.docx').exists()


def test_word_can_also_be_selected_for_basic_conversion(bundle, tmp_path):
    original=json.loads((bundle/'manifest.json').read_text())
    result=workflow.convert(bundle/original['source_pdf'],tmp_path/'basic-word',output_format='word')
    assert (tmp_path/'basic-word/基础稿.docx').exists()
    assert result['output_format']=='word'


def test_unlocated_original_note_can_be_preserved_without_faking_an_anchor(bundle):
    ready(bundle, issue=True)
    path=bundle/'handwash/layout.json'
    layout=json.loads(path.read_text())
    layout['unanchored_notes']=[{'pdf_page':2,'text':'「而」，某本作「之」。'}]
    path.write_text(json.dumps(layout,ensure_ascii=False))
    export(bundle,allow_unresolved=True)
    text=(bundle/'handwashed.md').read_text()
    assert '## 未定位校勘' in text
    assert '「而」，某本作「之」。' in text
    mapping=json.loads((bundle/'handwashed.sources.json').read_text())
    assert mapping['unanchored_notes'][0]['pdf_page']==2
    row=mapping['unanchored_notes'][0]
    assert '「而」' in text[row['start']:row['end']]


def test_unlocated_note_must_have_an_actual_recorded_source_issue(bundle):
    ready(bundle)
    path=bundle/'handwash/layout.json';layout=json.loads(path.read_text())
    layout['unanchored_notes']=[{'pdf_page':1,'text':'无法定位的原注。'}]
    path.write_text(json.dumps(layout,ensure_ascii=False))
    with pytest.raises(ValueError,match='待核'):
        export(bundle,allow_unresolved=True)


def test_joined_bold_classical_text_remains_one_readable_span(bundle):
    ready(bundle)
    for n,text in [(1,'**经文上半，**'),(2,'**下半。**')]:
        (bundle/f'review/pages/{n:04d}.md').write_text(text)
        workflow.review_page(bundle,n,checks='body,order,notes,structure',reviewer='Codex',note='对照跨页粗体经文。',issues=[])
    export(bundle)
    assert '**经文上半，下半。**' in (bundle/'handwashed.md').read_text()


def test_switching_formats_does_not_offer_a_stale_word_or_forget_ownership(bundle):
    from textferry.workflow import read_manifest,engine,export_bundle
    ready(bundle)
    export(bundle,output_format='word')
    path=bundle/'review/pages/0001.md';path.write_text(path.read_text()+'补充校正。')
    workflow.review_page(bundle,1,checks='body,order,notes,structure',reviewer='Codex',note='继续修订当前页稿。',issues=[])
    export(bundle,output_format='markdown')
    b,m=read_manifest(bundle)
    assert '整理稿.docx' not in export_bundle(b,m,engine(b,m))['artifacts']
    assert export(bundle,output_format='word')['current']


def test_append_word_reuses_current_reviewed_draft(bundle, monkeypatch):
    from textferry.service import TextFerryService
    ready(bundle)
    export(bundle)
    before = (bundle / 'handwashed.md').read_bytes()
    monkeypatch.setattr(TextFerryService, 'run_job', lambda *a, **k: pytest.fail('Appending Word must not rerun OCR'))
    result = workflow.change_output(bundle, output_format='word', equations='native')
    assert (bundle / 'handwashed.md').read_bytes() == before
    assert (bundle / '整理稿.docx').is_file()
    assert result['handwash']['current']
    assert json.loads((bundle / 'manifest.json').read_text())['word_equations'] == 'native'
