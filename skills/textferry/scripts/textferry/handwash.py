# SPDX-License-Identifier: AGPL-3.0-only
"""Export genuinely reviewed page work as a readable, traceable handwashed text.

Editorial judgments remain with the reviewing agent. This module checks the
recorded scope, explicit page boundaries, note pairing and export freshness.
"""
from __future__ import annotations

import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from .workflow import collect_report, digest, engine, read_manifest, write_json, source_file, review_source, review_evidence_digest, review_evidence_available, resolve_bundle
from .math_markdown import normalize_math_delimiters

DEF = re.compile(r'^ {0,3}\[\^([^\]\s]+)\]:[ \t]*(.*)$')
REF = re.compile(r'(?<!\\)\[\^([^\]\s]+)\]')


def prepare_handwash(bundle):
    bundle, manifest = read_manifest(bundle)
    if manifest['mode'] != 'review':
        raise ValueError('手洗需要 review 档的页图、页级工作稿和实际核查记录。')
    path = bundle / 'handwash/layout.json'
    if not path.exists():
        write_json(path, {'schema_version': 1, 'title': Path(manifest['source_name']).stem,
                         'pages': [{'pdf_page': n, 'boundary': 'separate' if n == 1 else 'unchecked',
                                    'joiner': '', 'printed_page': None, 'original_pdf_page': manifest.get('source_chapter_map' if manifest.get('source_format') == 'epub' else 'source_page_map', list(range(1, manifest['total_pages'] + 1)))[n - 1]}
                                   for n in range(1, manifest['total_pages'] + 1)]})
    source = bundle / 'handwash/source.json'
    if not source.exists():
        write_json(source, {'title': None, 'author': None, 'year': None, 'publisher': None,
                            'edition': None, 'scope_description': None, 'citation_first': None,
                            'citation_subsequent': None, 'metadata_status': '待按底本核实；未知字段保留 null'})
    return {'status': 'handwash_workbench', 'layout': 'handwash/layout.json',
            'source_metadata': 'handwash/source.json', 'instructions': '按材料 SOP 手洗 review/pages/*.md，核定跨页边界，再记录核查并导出。'}


def read_layout(bundle, manifest):
    path = bundle / 'handwash/layout.json'
    if not path.exists():
        raise ValueError('请先 prepare-handwash 并核定章节、范围及跨页接续。')
    data = json.loads(path.read_text(encoding='utf-8'))
    rows = data.get('pages', [])
    if not isinstance(data.get('title'), str) or not data['title'].strip() or not rows:
        raise ValueError('请填写标题并保留至少一个明确的来源页。')
    numbers = [row['pdf_page'] for row in rows]
    if any(type(n) is not int or not 1 <= n <= manifest['total_pages'] for n in numbers) or numbers != sorted(set(numbers)):
        raise ValueError('来源页需为范围内、无重复的升序 PDF 页。')
    return data


def snapshot(bundle, manifest, layout):
    latest = {}
    path = bundle / 'review/ledger.json'
    if path.exists():
        latest = {row['page']: row for row in json.loads(path.read_text(encoding='utf-8'))['entries']}
    service = engine(bundle, manifest)
    result = {'source_sha256': manifest['source_sha256'], 'layout': digest(bundle / 'handwash/layout.json'),
              'metadata': digest(bundle / 'handwash/source.json'), 'machine': digest(bundle / 'machine.md'), 'pages': []}
    for row in layout['pages']:
        number = row['pdf_page']
        paths = [bundle / f'review/pages/{number:04d}.md', review_source(bundle, manifest, number),
                 service.store.page_normalized_path(manifest['job_id'], number)]
        result['pages'].append({'pdf_page': number, 'hashes': [digest(paths[0]) if paths[0].exists() else None, review_evidence_digest(bundle, manifest, number) if review_evidence_available(bundle, manifest, number) else None, digest(paths[2]) if paths[2].exists() else None], 'review': latest.get(number)})
    return result


def handwash_status(bundle):
    bundle = resolve_bundle(bundle)
    path = bundle / 'handwash/export.json'
    if not path.exists():
        return {'status': 'not_exported', 'current': False}
    try:
        bundle, manifest = read_manifest(bundle)
        saved = json.loads(path.read_text(encoding='utf-8'))
        layout = read_layout(bundle, manifest)
        current = saved['snapshot'] == snapshot(bundle, manifest, layout)
        current = current and all((bundle / name).is_file() and digest(bundle / name) == value for name, value in saved.get('extra_artifacts', {}).items())
        current = current and saved['document_sha256'] == digest(bundle / 'handwashed.md') and saved['sources_sha256'] == digest(bundle / 'handwashed.sources.json')
        return {'status': saved['status'] if current else 'stale', 'current': current,
                'covered_pages': saved['covered_pages'], 'page_numbers': saved['page_numbers'],
                'academic_clearance': False, 'output_format': saved.get('output_format', 'markdown')}
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'stale', 'current': False}


def split_notes(text, page):
    """Read Markdown footnote definitions; keep fenced examples as body text."""
    lines, body, notes = text.splitlines(), [], []
    i, fence = 0, None
    while i < len(lines):
        line = lines[i]
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            body.append(line)
            i += 1
            continue
        match = DEF.match(line) if fence is None else None
        if not match:
            body.append(line)
            i += 1
            continue
        key, first = match.groups()
        block = [f'[^{key}]: {first}']
        i += 1
        while i < len(lines):
            if lines[i].startswith(('    ', '\t')):
                block.append(lines[i])
                i += 1
            elif not lines[i].strip() and i + 1 < len(lines) and lines[i + 1].startswith(('    ', '\t')):
                block.append('')
                i += 1
            else:
                break
        notes.append({'id': key, 'text': '\n'.join(block), 'pdf_page': page})
    cleaned = '\n'.join(body).strip()
    # Definitions were relocated; drop only now-empty trailing note headings.
    while re.search(r'(?:\n|^)#{1,6}\s*(?:注释|腳注|脚注|参考文献|參考文獻)\s*$', cleaned):
        cleaned = re.sub(r'(?:\n|^)#{1,6}\s*(?:注释|腳注|脚注|参考文献|參考文獻)\s*$', '', cleaned).rstrip()
    return cleaned, notes


def references(text):
    # Code examples are material, not actual note uses.
    scrubbed, fence = [], None
    for line in text.splitlines():
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if match:
            token = match.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None and not line.startswith(('    ', '\t')):
            scrubbed.append(re.sub(r'(`+).*?\1', '', line))
    return set(REF.findall('\n'.join(scrubbed)))


def export_handwash(bundle, *, reviewer, note, page_numbers='hide', allow_unresolved=False, output_format=None):
    bundle, manifest = read_manifest(bundle)
    if manifest['mode'] != 'review' or page_numbers not in {'show', 'hide'}:
        raise ValueError('手洗需要 review 档；页码选项为 show 或 hide。')
    if not reviewer.strip() or not note.strip():
        raise ValueError('请记录实际执行全文接续、章节和注释检查的核查者及具体说明。')
    output_format = output_format or manifest.get('output_format', 'markdown')
    if output_format not in {'markdown', 'word', 'both'}:
        raise ValueError('请选择 Markdown 或 Word 输出格式。')
    layout = read_layout(bundle, manifest)
    numbers = [row['pdf_page'] for row in layout['pages']]
    report = collect_report(bundle, manifest, engine(bundle, manifest))
    missing = sorted(set(numbers) - set(report['reviewed_pages']))
    if missing:
        raise ValueError(f'这些页尚未完成当前版本的四项核查或记录已失效：{missing}')
    issues = sorted(set(numbers) & set(report['unresolved_pages']))
    if issues and not allow_unresolved:
        raise ValueError(f'仍有待核问题的页：{issues}；需要交付带疑难的阶段稿时，显式使用 --allow-unresolved。')
    final, sources_path = bundle / 'handwashed.md', bundle / 'handwashed.sources.json'
    state = bundle / 'handwash/export.json'
    saved = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
    if final.exists() or sources_path.exists():
        saved = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
        if not final.exists() or not sources_path.exists() or saved.get('document_sha256') != digest(final) or saved.get('sources_sha256') != digest(sources_path):
            raise ValueError('现有成稿或来源映射已被直接修改，不能覆盖；请先保留改动并回写页级工作稿，或移走成稿后重新导出。')
    for name in ('整理稿.docx', '整理说明.md'):
        path = bundle / name
        if path.exists() and saved.get('extra_artifacts', {}).get(name) != digest(path):
            raise ValueError('现有 Word 成稿或整理说明已被修改，请先保留改动，不会直接覆盖。')
    for name, expected in saved.get('extra_artifacts', {}).items():
        path = bundle / name
        if not path.exists() or digest(path) != expected:
            raise ValueError('现有 Word 成稿或整理说明已被修改，请先保留改动，不会直接覆盖。')
    text = f"# {layout['title'].strip()}\n\n"
    pages, notes = [], []
    previous = None
    for row in layout['pages']:
        number, boundary = row['pdf_page'], row.get('boundary')
        if boundary not in {'join', 'separate'} or (previous is None or number != previous + 1) and boundary != 'separate':
            raise ValueError(f'PDF 第 {number} 页的跨页接续尚未核定；首个选页或不连续选页需 separate。')
        source_map = manifest.get('source_chapter_map' if manifest.get('source_format') == 'epub' else 'source_page_map')
        if source_map and previous is not None and source_map[number - 1] != source_map[previous - 1] + 1 and boundary != 'separate':
            raise ValueError('原文件中不连续的选页或章节需 separate，不能直接拼接。')
        joiner = row.get('joiner', '')
        if joiner not in {'', ' '}:
            raise ValueError('跨页 joiner 仅支持空字符串或一个空格；其他文字请写入页级工作稿。')
        body, page_notes = split_notes(normalize_math_delimiters((bundle / f'review/pages/{number:04d}.md').read_text(encoding='utf-8')), number)
        if previous is not None:
            if boundary == 'join' and page_numbers == 'hide' and text.endswith('**') and body.startswith('**'):
                text = text[:-2]
                pages[-1]['end'] -= 2
                body = body[2:]
            text += joiner if boundary == 'join' else '\n\n'
        start = len(text)
        if page_numbers == 'show':
            label = row.get('printed_page')
            text += f"〔EPUB 第 {row.get('original_pdf_page') or number} 节〕" if manifest.get('source_format') == 'epub' else f"〔PDF 第 {row.get('original_pdf_page') or number} 页{f'；书页 {label}' if label is not None else ''}〕"
            if boundary == 'separate':
                text += '\n\n'
        text += body
        pages.append({**row, 'start': start, 'end': len(text)})
        notes.extend(page_notes)
        previous = number
    by_id = {}
    for item in notes:
        if item['id'] in by_id:
            raise ValueError(f"注释编号重复：{item['id']}；请按全篇编号解决，不能静默覆盖。")
        by_id[item['id']] = item
    used = references(text)
    for item in notes:
        used |= references(textwrap.dedent(item['text'].split(':', 1)[1]))
    absent, orphan = used - set(by_id), set(by_id) - used
    if absent or orphan:
        raise ValueError(f'注释配对失败；缺少定义：{sorted(absent)}；缺少正文标记：{sorted(orphan)}。不得补造来源。')
    note_map = {}
    for ref_notes, heading in ((False, '注释'), (True, '参考文献')):
        group = [item for item in notes if item['id'].startswith('ref-') == ref_notes]
        if group:
            text = text.rstrip() + f'\n\n## {heading}\n\n'
            for item in group:
                start = len(text)
                text += item['text'] + '\n\n'
                note_map[item['id']] = {'pdf_page': item['pdf_page'], 'start': start, 'end': start + len(item['text'])}
    unanchored_map = []
    unanchored = layout.get('unanchored_notes', [])
    for item in unanchored:
        if item.get('pdf_page') not in issues or not isinstance(item.get('text'), str) or not item['text'].strip() or REF.search(item['text']):
            raise ValueError('未定位校勘必须对应所选页的已登记待核问题，并完整保留原注；不能补造正文标记。')
    if unanchored:
        text = text.rstrip() + '\n\n## 未定位校勘\n\n'
        for item in unanchored:
            source_row = next(row for row in layout['pages'] if row['pdf_page'] == item['pdf_page'])
            label = source_row.get('original_pdf_page') or item['pdf_page']
            text += f'原文件第 {label} 页：'
            start = len(text)
            text += item['text'].strip()
            unanchored_map.append({'pdf_page': item['pdf_page'], 'start': start, 'end': len(text)})
            text += '\n\n'
    text += '\n' if not text.endswith('\n') else ''
    current_snapshot = snapshot(bundle, manifest, layout)
    metadata = json.loads((bundle / 'handwash/source.json').read_text(encoding='utf-8'))
    status = 'handwashed_with_issues' if issues else 'handwashed'
    mapping = {'schema_version': 1, 'stage': '已手洗（有待核项）' if issues else '已手洗',
               'academic_clearance': False, 'status': status, 'page_numbers': page_numbers,
               'offset_unit': 'unicode_codepoints', 'source_pdf': manifest.get('source_pdf'), 'source_file': source_file(manifest), 'source_format': manifest.get('source_format', 'pdf'), 'source_unit': manifest.get('source_unit', 'page'),
               'source_sha256': manifest['source_sha256'], 'machine_file': 'machine.md',
               'machine_sha256': digest(bundle / 'machine.md'), 'source_metadata': metadata,
               'covered_pages': numbers, 'excluded_pages': sorted(set(range(1, manifest['total_pages'] + 1)) - set(numbers)),
               'coverage': ('complete_input_epub' if manifest.get('source_format') == 'epub' else 'complete_input_pdf') if len(numbers) == manifest['total_pages'] else 'selected_pages',
               'unresolved_pages': issues, 'pages': pages, 'notes': note_map, 'unanchored_notes': unanchored_map,
               'output_format': output_format, 'reviewer': reviewer.strip(), 'assembly_note': note.strip(),
               'review_records': [item['review'] for item in current_snapshot['pages']],
               'exported_at': datetime.now(timezone.utc).isoformat()}
    extras = {name: sha for name, sha in saved.get('extra_artifacts', {}).items() if name == '整理稿.docx' and (bundle / name).exists()}
    if output_format in {'word', 'both'}:
        from .word_export import write_word
        write_word(text, bundle / '整理稿.docx', bundle, equations=manifest.get('word_equations'))
        extras['整理稿.docx'] = digest(bundle / '整理稿.docx')
    explanation = readable_explanation(layout, metadata, current_snapshot, issues, epub=manifest.get("source_format") == "epub")
    (bundle / '整理说明.md').write_text(explanation, encoding='utf-8')
    extras['整理说明.md'] = digest(bundle / '整理说明.md')
    final.write_text(text, encoding='utf-8')
    write_json(sources_path, mapping)
    write_json(state, {'status': status, 'snapshot': current_snapshot, 'document_sha256': digest(final),
                      'sources_sha256': digest(sources_path), 'covered_pages': numbers, 'page_numbers': page_numbers, 'extra_artifacts': extras, 'output_format': output_format})
    from .workflow import export_bundle
    export_bundle(bundle, manifest, engine(bundle, manifest))
    return handwash_status(bundle)


def readable_explanation(layout, metadata, current_snapshot, issues, *, epub=False):
    rows = layout['pages']
    lines = ['# 整理说明', '', '## ' + layout['title'].strip(), '']
    if metadata.get('scope_description'):
        lines += [str(metadata['scope_description']), '']
    unit = '个章节单元' if epub else '页'
    lines += [f'本次对照整理了 {len(rows)} {unit}，检查了正文、阅读顺序、注释和结构。', '', '## 本次处理范围', '']
    numbers = [row.get('original_pdf_page') or row['pdf_page'] for row in rows]
    spans = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append(str(start) if start == previous else f'{start}—{previous}')
        start = previous = number
    spans.append(str(start) if start == previous else f'{start}—{previous}')
    lines.append(('EPUB 第 ' if epub else '原文件第 ') + '、'.join(spans) + (' 节。' if epub else ' 页。'))
    lines += ['', '## 需要留意的地方', '']
    if not issues:
        lines += ['本次核对范围内，没有登记仍需处理的问题。']
    else:
        latest = {item['pdf_page']: item['review'] for item in current_snapshot['pages']}
        for row in rows:
            number = row['pdf_page']
            if number not in issues:
                continue
            label = f"书上第{row['printed_page']}页（原文件第{row.get('original_pdf_page') or number}页）" if row.get('printed_page') is not None else f"原文件第{row.get('original_pdf_page') or number}页"
            if epub:
                label = f'EPUB 第 {number} 节'
            lines.append('### ' + label)
            lines.append('')
            for problem in latest[number]['issues']:
                lines.append('- ' + str(problem))
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'
