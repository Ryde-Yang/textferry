# SPDX-License-Identifier: AGPL-3.0-only
"""Discard reproducible caches after completion; preserve source and evidence."""
import json
from pathlib import Path


def archived_image_digest(bundle, manifest, page):
    if manifest.get('source_format') == 'epub':
        return None
    path = Path(bundle) / 'cache-manifest.json'
    try:
        data = json.loads(path.read_text())
        if data['source_sha256'] != manifest['source_sha256']:
            return None
        return data.get('review_images', {}).get(str(page))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def page_image(bundle, *, page):
    from .workflow import read_manifest, review_source, render_pdf_page_png
    bundle, manifest = read_manifest(bundle)
    if manifest.get('source_format') == 'epub' or not 1 <= page <= manifest['total_pages']:
        raise ValueError('请选择本次 PDF 任务范围内的一页。')
    target = review_source(bundle, manifest, page)
    if not target.exists():
        render_pdf_page_png(bundle / manifest['source_pdf'], page, target, dpi=120)
    return {'status': 'page_image_ready', 'page': page, 'image': str(target)}


def compact(bundle, *, apply=False):
    from .workflow import read_manifest, engine, digest, write_json
    from .handwash import handwash_status
    from .delivery import check_user_edits
    from .types import PageStatus
    bundle, manifest = read_manifest(bundle)
    check_user_edits(bundle)
    rows = engine(bundle, manifest).store.list_pages(manifest['job_id'])
    if any(row['status'] != PageStatus.DONE.value for row in rows):
        raise ValueError('任务尚未完成，已保留缓存和远程回执，请先恢复转换。')
    if manifest['mode'] == 'review' and not handwash_status(bundle)['current']:
        raise ValueError('整理成稿尚未完成或核查内容发生变化，请先完成当前版本的核查和导出。')
    if manifest.get('source_format') == 'epub':
        return {'status': 'compacted' if apply else 'cleanup_preview', 'removed_bytes': 0, 'files': [], 'message': '原生 EPUB 没有可清理的 PDF 页图缓存。'}
    job = bundle / '_work/jobs' / manifest['job_id']
    candidates = set(job.glob('batches/*.pdf')) | set(job.glob('remote-requests/*/input*.pdf'))
    candidates.update(job.glob('pages/page_*/image.png'))
    images = [bundle / f'review/pages/{n:04d}.png' for n in range(1, manifest['total_pages'] + 1)]
    candidates.update(p for p in images if p.exists())
    selected = bundle / 'source/selected.pdf'
    if selected.exists() and digest(selected) == manifest['source_sha256']:
        candidates.add(selected)
    protected = {(bundle / manifest['source_pdf']).resolve()}
    if manifest.get('original_source_file'):
        protected.add((bundle / manifest['original_source_file']).resolve())
    candidates = sorted(p for p in candidates if p.resolve() not in protected)
    for path in candidates:
        if path.is_symlink() or not path.resolve().is_relative_to(bundle):
            raise ValueError('缓存路径包含符号链接或指向任务外，已停止清理。')
    result = {'status': 'cleanup_preview', 'removed_bytes': sum(p.stat().st_size for p in candidates),
              'files': [p.relative_to(bundle).as_posix() for p in candidates],
              'message': '只清理可重建页图和已完成上传的 PDF 副本；保留来源、全文、核查记录及远程回执。文件大小为逻辑字节数。'}
    if apply:
        prior = {str(n): archived_image_digest(bundle, manifest, n) for n in range(1, manifest['total_pages'] + 1)}
        for n, path in enumerate(images, 1):
            if path.exists():
                prior[str(n)] = digest(path)
        # Write reconstruction evidence first: an interrupted cleanup stays usable.
        write_json(bundle / 'cache-manifest.json', {'schema_version': 1, 'source_sha256': manifest['source_sha256'],
                   'review_images': {k: v for k, v in prior.items() if v}, 'dpi': 120})
        for path in candidates:
            path.unlink()
        result['status'] = 'compacted'
    return result
