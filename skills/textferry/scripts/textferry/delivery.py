# SPDX-License-Identifier: AGPL-3.0-only
"""Publish a few readable files while keeping the resumable bundle together."""
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def delivery_root(bundle):
    return bundle.parent if bundle.name == '.textferry' else bundle / '成品'


def check_user_edits(bundle):
    from .workflow import digest
    marker = bundle / 'delivery.json'
    if not marker.exists():
        return
    saved = json.loads(marker.read_text())
    output = (bundle / saved['output_relative']).resolve()
    for name, value in saved.get('files', {}).items():
        path = output / name
        if path.is_file() and digest(path) != value:
            raise ValueError('交付稿已有用户修订，已保留：' + name + '。请先将修改对应到页稿，再继续重导出。')


def relocate_markdown(text, bundle, output):
    def target(value):
        bracketed = value.startswith('<') and value.endswith('>')
        value = value[1:-1] if bracketed else value
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path:
            return '<' + value + '>' if bracketed else value
        path = (bundle / unquote(parts.path)).resolve()
        if not path.is_relative_to(bundle):
            return '<' + value + '>' if bracketed else value
        relative = quote(Path(os.path.relpath(path, output)).as_posix(), safe='/@+~._-')
        value = urlunsplit(('', '', relative, parts.query, parts.fragment))
        return '<' + value + '>' if bracketed else value
    def line(value):
        value = re.sub(r'(\]\(\s*)(<[^>]+>|[^\s)]+)', lambda m: m[1] + target(m[2]), value)
        value = re.sub(r'(^ {0,3}\[[^\]^]+\]:\s*)(<[^>]+>|\S+)', lambda m: m[1] + target(m[2]), value)
        return re.sub(r'((?:src|href)\s*=\s*[\"\x27])([^\"\x27]+)', lambda m: m[1] + target(m[2]), value, flags=re.I)
    result, fence, in_note = [], None, False
    for value in text.splitlines(keepends=True):
        original = value
        definition = re.match(r'^ {0,3}\[\^[^\]]+\]:', value)
        if definition:
            in_note = True
        elif value.strip() and not value.startswith(('    ', '\t')):
            in_note = False
        indent = ''
        if in_note and not definition and value.startswith(('    ', '\t')):
            indent = '\t' if value.startswith('\t') else '    '
            value = value[len(indent):]
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', value)
        if mark:
            if fence is None:
                fence = mark[1]
            elif mark[1][0] == fence[0] and len(mark[1]) >= len(fence):
                fence = None
            result.append(original)
        elif fence is not None or value.startswith(('    ', '\t')):
            result.append(original)
        else:
            parts = re.split(r'(`+[^`]*`+)', value)
            result.append(indent + ''.join(part if i % 2 else line(part) for i, part in enumerate(parts)))
    return ''.join(result)


def deliver(bundle):
    from .workflow import read_manifest, collect_report, engine, digest, write_json
    bundle, manifest = read_manifest(bundle)
    check_user_edits(bundle)
    report = collect_report(bundle, manifest, engine(bundle, manifest))
    if report['pending_pages'] or report['failed_pages'] or report.get('image_only_chapters') and manifest['mode'] == 'quick':
        raise ValueError('尚有未完成内容，请先恢复转换，再生成完整交付。')
    output = delivery_root(bundle)
    plan = {'机器底稿.md': bundle / 'machine.md'}
    fmt = manifest.get('output_format', 'markdown')
    if manifest['mode'] == 'review':
        if not report['handwash']['current']:
            raise ValueError('当前整理稿尚未完成核查和导出，请先完成整理。')
        plan['整理说明.md'] = bundle / '整理说明.md'
        if fmt in {'markdown', 'both'}:
            plan['整理稿.md'] = bundle / 'handwashed.md'
        if fmt in {'word', 'both'}:
            plan['整理稿.docx'] = bundle / '整理稿.docx'
    elif fmt in {'word', 'both'}:
        plan['基础稿.docx'] = bundle / '基础稿.docx'
    saved_path = bundle / 'delivery.json'
    saved = json.loads(saved_path.read_text()) if saved_path.exists() else {'files': {}}
    prepared = {}
    for name, source in plan.items():
        if not source.is_file():
            raise ValueError('交付文件尚未生成：' + name)
        data = relocate_markdown(source.read_text(), bundle, output).encode() if source.suffix == '.md' else source.read_bytes()
        candidate = output / name
        if candidate.is_symlink() or candidate.exists() and not candidate.is_file():
            raise ValueError('交付位置已有其他内容，已保留：' + name)
        if candidate.exists() and name not in saved.get('files', {}) and candidate.read_bytes() != data:
            raise ValueError('交付位置已有文件或修订，已保留：' + name)
        prepared[name] = data
    output.mkdir(parents=True, exist_ok=True)
    for name, data in prepared.items():
        target = output / name
        temp = target.with_name('.' + name + '.tmp')
        if temp.exists():
            raise ValueError('交付临时位置已有内容，请保留并检查：' + str(temp))
        temp.write_bytes(data)
        temp.replace(target)
    write_json(saved_path, {'schema_version': 1, 'output_relative': os.path.relpath(output, bundle),
               'files': {**saved.get('files', {}), **{name: digest(output / name) for name in prepared}}})
    files = {}
    if manifest['mode'] == 'review':
        files['整理稿'] = str(output / ('整理稿.md' if fmt in {'markdown', 'both'} else '整理稿.docx'))
        if fmt == 'both':
            files['Word 整理稿'] = str(output / '整理稿.docx')
        files['机器底稿'] = str(output / '机器底稿.md')
        files['整理说明'] = str(output / '整理说明.md')
    else:
        files['基础稿'] = str(output / ('基础稿.docx' if fmt in {'word', 'both'} else '机器底稿.md'))
        if fmt in {'word', 'both'}:
            files['机器底稿'] = str(output / '机器底稿.md')
    links = '\n'.join('- [' + label + '](<' + path + '>)' for label, path in files.items())
    unit = '章节' if manifest.get('source_format') == 'epub' else '页'
    scope = len(report['handwash']['covered_pages']) if manifest['mode'] == 'review' else report['processed_pages']
    delivery_message = ('整理稿' if manifest['mode'] == 'review' else '基础稿') + '覆盖 ' + str(scope) + ' ' + unit + '。\n\n' + links + '\n\n请保留整个成果文件夹，方便回查和后续追加格式。'
    return {'status': 'delivered', 'output': str(output), 'files': files, 'delivery_message': delivery_message,
            'message': '成品已生成。整个成果文件夹一起保留和移动，可继续回查、整理及追加格式。'}
