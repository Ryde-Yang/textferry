# SPDX-License-Identifier: AGPL-3.0-only
"""Local EPUB spine extraction. Chapters, not invented printed pages, are units."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from .normalize import normalize_ocr_text
from .storage import JobCreateInput
from .types import PageMode, ScriptTarget

OPS = '{http://www.idpf.org/2007/ops}'
IMAGE_TYPES = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/svg+xml': '.svg', 'image/webp': '.webp'}


def local_reference(base, href):
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or parts.query or '\\' in href:
        raise ValueError('EPUB 的内容和图片必须在文件内，暂不支持外部资源。')
    name = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parts.path))) if parts.path else base
    if name.startswith(('/', '../')) or name == '..':
        raise ValueError('EPUB 包含越界资源路径。')
    return name, unquote(parts.fragment)


def tag(node):
    return node.tag.rsplit('}', 1)[-1].lower()


def package(path):
    try:
        with ZipFile(path) as z:
            infos = z.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) > 20000 or sum(i.file_size for i in infos) > 512 * 1024 * 1024 or any(i.file_size > 32 * 1024 * 1024 for i in infos):
                raise ValueError('EPUB 存在重复文件或超过安全读取上限。')
            for name in names:
                if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name or '\x00' in name:
                    raise ValueError('EPUB 包含不安全路径。')
            if 'META-INF/encryption.xml' in names:
                raise ValueError('暂不支持带加密或字体混淆声明的 EPUB，请提供有权处理的无保护版本。')
            if z.read('mimetype').strip() != b'application/epub+zip':
                raise ValueError('这不是有效的 EPUB 文件。')
            container = ET.fromstring(z.read('META-INF/container.xml'))
            roots = [n.attrib['full-path'] for n in container.iter() if tag(n) == 'rootfile']
            if len(roots) != 1:
                raise ValueError('暂不支持多个根文档的 EPUB。')
            opf, _ = local_reference('', roots[0])
            book = ET.fromstring(z.read(opf))
            items = {n.attrib['id']: {**n.attrib, 'path': local_reference(opf, n.attrib['href'])[0]} for n in book.iter() if tag(n) == 'item'}
            spine = [items[n.attrib['idref']] for n in book.iter() if tag(n) == 'itemref']
            if not spine:
                raise ValueError('EPUB 没有可读取的章节顺序。')
            files = {item['path']: z.read(item['path']) for item in items.values()}
            documents = {}
            warnings = []
            for item in items.values():
                if item['media-type'] in {'application/xhtml+xml', 'text/html'}:
                    doc = ET.fromstring(files[item['path']])
                    for node in doc.iter():
                        if tag(node) in {'script', 'iframe', 'object', 'audio', 'video', 'canvas', 'form'}:
                            raise ValueError('此 EPUB 含脚本、交互或音视频内容，当前版本不能完整转换。')
                        if tag(node) in {'img', 'image'}:
                            local_reference(item['path'], node.get('src') or node.get('{http://www.w3.org/1999/xlink}href') or node.get('href') or '')
                    documents[item['path']] = doc
                if item.get('properties', '').split().count('scripted'):
                    raise ValueError('暂不支持交互式 EPUB。')
            image_only = []
            for number, item in enumerate(spine, 1):
                if item['path'] not in documents:
                    raise ValueError('当前仅支持以 XHTML 为章节的 EPUB；SVG 画页请先转 PDF。')
                body = next((n for n in documents[item['path']].iter() if tag(n) == 'body'), None)
                if body is None:
                    raise ValueError('EPUB 章节缺少正文 body，无法完整读取：' + item['path'])
                if not ''.join(body.itertext()).strip():
                    image_only.append(number)
            if len(image_only) == len(spine):
                raise ValueError('纯图片 EPUB 暂不支持自动 OCR，请提供 PDF 或图片扫描件。')
            if image_only:
                warnings.append('第 ' + '、'.join(map(str, image_only)) + ' 节没有可提取文字；图片会保留，尚未识别图中文字。')
            for n in book.iter():
                if tag(n) == 'meta' and n.get('property') == 'rendition:layout' and (n.text or '').strip() == 'pre-paginated':
                    warnings.append('固定版式 EPUB：保留内容结构，不复刻原排版；请核对图片、竖排和分栏。')
            return items, spine, files, documents, warnings
    except (BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise ValueError('EPUB 文件不完整或结构不受支持：' + str(exc)[:200]) from exc


def inspect_epub(pdf):
    _, spine, _, documents, warnings = package(pdf)
    return {'source_format': 'epub', 'source_unit': 'chapter', 'total_pages': len(spine),
            'total_chapters': len(spine), 'ocr_pages': [], 'warnings': warnings,
            'image_only_chapters': [i for i, item in enumerate(spine, 1) if not ''.join(next(n for n in documents[item['path']].iter() if tag(n) == 'body').itertext()).strip()],
            'pages': [{'page': i, 'route': 'text', 'href': item['path'],
                       'title': next((''.join(node.itertext()).strip() for node in documents[item['path']].iter() if tag(node) in {'h1', 'h2'}), item['path']), 'characters': len(''.join(documents[item['path']].itertext()))} for i, item in enumerate(spine, 1)]}


def svg_image(raw):
    import pymupdf as fitz
    try:
        svg = ET.fromstring(raw)
        for node in svg.iter():
            if tag(node) in {'script', 'foreignobject', 'style'} or any(key.rsplit('}', 1)[-1].lower().startswith('on') or key.rsplit('}', 1)[-1] in {'href', 'src'} and not value.startswith('#') or 'url(' in value.lower() or '@import' in value.lower() for key, value in node.attrib.items()):
                raise ValueError('EPUB 含外部引用或活动 SVG，暂不支持。')
        with fitz.open(stream=raw, filetype='svg') as doc:
            page = doc[0]
            scale = min(2, 2400 / max(page.rect.width, 1), 2400 / max(page.rect.height, 1))
            return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
    except (ET.ParseError, RuntimeError) as exc:
        raise ValueError('无法完整转换 EPUB 的 SVG 图示：' + str(exc)[:150]) from exc


def markdown_notes(tree, chapter, pandoc):
    """Name actual notes in the AST; literal code remains untouched."""
    notes = []
    def visit(value):
        if isinstance(value, dict) and value.get('t') == 'Note':
            blocks = value['c']
            identifier = f'e{chapter}-{len(notes) + 1}'
            notes.append((identifier, blocks))
            value.clear()
            value.update({'t': 'RawInline', 'c': ['markdown', f'[^{identifier}]']})
            visit(blocks)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(tree)
    body = pandoc(json.dumps(tree), 'json', 'markdown+tex_math_dollars+footnotes-raw_html').rstrip()
    for identifier, blocks in notes:
        lines = pandoc(json.dumps({**tree, 'meta': {}, 'blocks': blocks}), 'json', 'markdown+tex_math_dollars+footnotes-raw_html').strip().splitlines()
        if lines:
            body += f'\n\n[^{identifier}]: ' + lines[0] + ''.join('\n    ' + line if line else '\n' for line in lines[1:])
    return body + '\n'


def convert_epub(path, output, *, mode, kind, method, provider, model, script, output_format, chapters=None, resume_existing=None):
    from .workflow import digest, engine, export_bundle, write_json
    from .word_export import pandoc_path, walk
    from .selection import parse_selection
    from .progress import emit
    if method == 'ocr':
        raise ValueError('EPUB 使用原生文本提取；图片扫描内容请先提供 PDF。')
    items, spine, files, docs, warnings = package(path)
    selected = resume_existing.get('source_chapter_map', list(range(1, len(spine) + 1))) if resume_existing else parse_selection(chapters, len(spine))
    original_total = len(spine)
    spine = [spine[number - 1] for number in selected]
    try:
        binary = pandoc_path()
    except ValueError:
        raise ValueError('EPUB 转换组件尚未安装，请让 Agent 安装 epub 可选依赖后继续。') from None
    if not resume_existing and output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('输出位置已有内容，请选择新目录；继续旧任务请使用 resume。')

    def pandoc(value, from_, to):
        result = subprocess.run([str(binary), '--from=' + from_, '--to=' + to, '--wrap=none'], input=value, text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise ValueError('EPUB 内容转换失败：' + result.stderr[:300])
        return result.stdout

    asset_paths, asset_bytes = {}, {}
    for item in items.values():
        if item['media-type'] in IMAGE_TYPES:
            raw = files[item['path']]
            sha = hashlib.sha256(raw).hexdigest()[:32]
            original_name = 'assets/' + sha + IMAGE_TYPES[item['media-type']]
            asset_bytes[original_name] = raw
            name = original_name
            if item['media-type'] == 'image/svg+xml':
                name = 'assets/' + sha + '.png'
                asset_bytes[name] = svg_image(raw)
            asset_paths[item['path']] = name
    note_nodes, referenced = {}, set()
    for filename, doc in docs.items():
        for node in doc.iter():
            types = (node.get(OPS + 'type', '') + ' ' + node.get('role', '')).split()
            if node.get('id') and set(types) & {'footnote', 'endnote', 'doc-footnote', 'doc-endnote'}:
                note_nodes[(filename, node.get('id'))] = node
            if filename in {item['path'] for item in spine} and tag(node) == 'a' and set(types) & {'noteref', 'doc-noteref'}:
                referenced.add(local_reference(filename, node.get('href', '')))
    if referenced - set(note_nodes):
        raise ValueError('EPUB 注释链接缺少对应注释，不能当作完整转换。')
    id_names = {(filename, node.get('id')): 'e' + hashlib.sha256((filename + '#' + node.get('id')).encode()).hexdigest()[:20] for filename, doc in docs.items() for node in doc.iter() if node.get('id')}
    for filename in docs:
        id_names[(filename, '')] = 'chapter-' + hashlib.sha256(filename.encode()).hexdigest()[:20]
    spine_positions = {item['path']: n for n, item in enumerate(spine, 1)}
    note_keys = {key: str(i) for i, key in enumerate(note_nodes, 1)}

    def html(doc, filename, *, source=False, note=False):
        doc = deepcopy(doc)
        if note:
            doc.tail = None
        if source and not note:
            imported = set()
            body = next((node for node in doc.iter() if tag(node) == 'body'), doc)
            for node in list(doc.iter()):
                types = (node.get(OPS + 'type', '') + ' ' + node.get('role', '')).split()
                if tag(node) == 'a' and set(types) & {'noteref', 'doc-noteref'}:
                    pair = local_reference(filename, node.get('href', ''))
                    if pair[0] != filename and pair in note_nodes and pair not in imported:
                        imported.add(pair)
                        extra = deepcopy(note_nodes[pair])
                        extra.tail = None
                        # Use this document's canonical IDs for the local evidence copy.
                        extra.set('id', id_names[pair])
                        for child in extra.iter():
                            if tag(child) in {'img', 'image'}:
                                target, _ = local_reference(pair[0], child.get('src') or child.get('{http://www.w3.org/1999/xlink}href') or '')
                                child.set('src' if tag(child) == 'img' else '{http://www.w3.org/1999/xlink}href', posixpath.relpath(target, posixpath.dirname(filename)))
                        body.append(extra)
        for parent in list(doc.iter()):
            for node in list(parent):
                if tag(node) == 'svg':
                    original = deepcopy(node)
                    original.tail = None
                    raw = ET.tostring(original, encoding='utf-8')
                    sha = hashlib.sha256(raw).hexdigest()[:32]
                    virtual = filename + '.inline-' + sha + '.svg'
                    image_name = 'assets/' + sha + '.png'
                    asset_paths[virtual] = image_name
                    asset_bytes['assets/' + sha + '.svg'] = raw
                    asset_bytes[image_name] = svg_image(raw)
                    image = ET.Element('img', {'src': posixpath.relpath(virtual, posixpath.dirname(filename)), 'alt': '原文图示'})
                    image.tail = node.tail
                    parent.insert(list(parent).index(node), image)
                    parent.remove(node)
        for parent in list(doc.iter()):
            for node in list(parent):
                if not source and not note and (filename, node.get('id')) in referenced:
                    # A referenced note is represented at its actual reference below.
                    if node.tail:
                        if len(list(parent)) and list(parent).index(node):
                            previous = list(parent)[list(parent).index(node) - 1]
                            previous.tail = (previous.tail or '') + node.tail
                        else:
                            parent.text = (parent.text or '') + node.tail
                    parent.remove(node)
        for node in doc.iter():
            node.tag = tag(node)
            # Strip CSS and event handlers from the locally viewable source.
            for key in list(node.attrib):
                if key.lower().startswith('on') or key in {'style', 'srcset'}:
                    del node.attrib[key]
            if node.tag in {'link', 'meta', 'base', 'style'}:
                node.attrib.clear()
                node.text = ''
            if node.get('id'):
                node.set('id', id_names.get((filename, node.get('id')), node.get('id')))
            if node.tag in {'img', 'image'}:
                attr = 'src' if node.tag == 'img' else '{http://www.w3.org/1999/xlink}href'
                href = node.get(attr) or node.get('href') or ''
                target, _ = local_reference(filename, href)
                if target not in asset_paths:
                    raise ValueError('EPUB 图片缺失或格式不支持，不能忽略后交付。')
                node.set(attr, ('../../' if source else '') + asset_paths[target])
            if node.tag == 'a' and node.get('href'):
                href = node.get('href')
                if not urlsplit(href).scheme:
                    target = local_reference(filename, href)
                    types = (node.get(OPS + 'type', '') + ' ' + node.get('role', '')).split()
                    if not source and set(types) & {'noteref', 'doc-noteref'}:
                        node.set('href', 'material-note:' + note_keys[target])
                        node.attrib.pop('role', None)
                    else:
                        if target not in id_names:
                            raise ValueError('EPUB 的内部链接目标缺失或格式不支持，不能忽略后交付。')
                        prefix = f"{spine_positions[target[0]]:04d}.xhtml" if source and target[0] != filename and target[0] in spine_positions and target not in referenced else ''
                        node.set('href', prefix + '#' + id_names[target])
        if not note:
            body = next((node for node in doc.iter() if node.tag == 'body'), doc)
            anchor = ET.Element('span', {'id': id_names[(filename, '')]})
            body.insert(0, anchor)
        return ET.tostring(doc, encoding='unicode', method='xml')

    notes = {}
    active_notes = set()
    def fill_notes(tree):
        for node in list(walk(tree)):
            if node.get('t') == 'Link' and node['c'][-1][0].startswith('material-note:'):
                key = node['c'][-1][0].split(':', 1)[1]
                if key in active_notes:
                    raise ValueError('EPUB 注释存在循环引用，请人工处理。')
                if key not in notes:
                    active_notes.add(key)
                    filename, ident = next(pair for pair, value in note_keys.items() if value == key)
                    note_tree = json.loads(pandoc(html(note_nodes[(filename, ident)], filename, note=True), 'html', 'json'))
                    fill_notes(note_tree)
                    notes[key] = note_tree['blocks']
                    active_notes.remove(key)
                identifier = node['c'][0][0]
                replacement = {'t': 'Note', 'c': deepcopy(notes[key])}
                node.clear()
                node.update({'t': 'Span', 'c': [[identifier, [], []], [replacement]]} if identifier else replacement)

    if resume_existing:
        manifest = resume_existing
        service = engine(output, manifest)
        job_id = manifest['job_id']
    else:
        diagnosis = inspect_epub(path)
        manifest = {'schema_version': 1, 'source_format': 'epub', 'source_unit': 'chapter', 'mode': mode, 'kind': kind,
                    'method': 'text', 'provider': provider, 'script': script, 'source_name': path.name,
                    'source_sha256': digest(path), 'total_pages': len(spine), 'total_chapters': len(spine),
                    'total_source_chapters': original_total, 'source_chapter_map': selected,
                    'simulation': False, 'output_format': output_format, 'word_equations': os.getenv('TEXTFERRY_WORD_EQUATIONS', 'native'), 'diagnosis': diagnosis, 'warnings': warnings,
                    'image_only_chapters': [i for i, original in enumerate(selected, 1) if original in diagnosis['image_only_chapters']],
                    'chapters': [{'number': n, 'original_number': selected[n - 1], 'href': item['path'], 'source': f'source/chapters/{n:04d}.xhtml'} for n, item in enumerate(spine, 1)]}
        output.mkdir(parents=True, exist_ok=True)
        service = engine(output, manifest)
        job_id = uuid.uuid4().hex
        original = service.store.job_dir(job_id) / 'input.epub'
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, original)
        service.store.create_job(JobCreateInput(job_id=job_id, page_mode=PageMode.PDF, script_target=ScriptTarget(script), model_option='epub_text', total_pages=len(spine), body_start_hint=None, input_pdf_path=str(original)))
        manifest.update(job_id=job_id, source_file=original.relative_to(output).as_posix())
        write_json(output / 'manifest.json', manifest)
    saved_assets = set()
    def persist_assets():
        for name, raw in asset_bytes.items():
            if name in saved_assets:
                continue
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(raw)
            saved_assets.add(name)
    persist_assets()
    for number, item in enumerate(spine, 1):
        if service.store.get_page(job_id, number)['status'] == 'DONE':
            continue
        emit('epub_extract', '正在提取 EPUB 章节', current=number, total=len(spine))
        filename = item['path']
        tree = json.loads(pandoc(html(docs[filename], filename), 'html', 'json'))
        fill_notes(tree)
        raw_text = markdown_notes(tree, number, pandoc)
        text = normalize_ocr_text(raw_text, ScriptTarget(script), preserve_content=True)
        source = html(docs[filename], filename, source=True)
        persist_assets()
        target = output / f'source/chapters/{number:04d}.xhtml'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding='utf-8')
        service.store.mark_page_done(job_id, number, image_path=None, raw_text=raw_text, normalized_text=text, detected_raw=None, detected_norm=None, retry_count=0)
    return export_bundle(output, manifest, service)


def resume_epub(bundle, manifest):
    from .workflow import source_file
    return convert_epub(bundle / source_file(manifest), bundle, mode=manifest['mode'], kind=manifest['kind'], method='text', provider=manifest['provider'], model=None, script=manifest['script'], output_format=manifest.get('output_format', 'markdown'), resume_existing=manifest)
