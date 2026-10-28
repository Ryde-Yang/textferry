# SPDX-License-Identifier: AGPL-3.0-only
"""PDF-to-Markdown bundles and explicit page review, independent of any vault."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pymupdf as fitz

from .config import get_textferry_settings, is_mock_ocr_enabled
from .credentials import load_api_key, onboarding, save_api_key
from .normalize import normalize_ocr_text
from .pdf_render import render_pdf_page_png
from .service import TextFerryService
from .types import JobStatus, PageMode, PageStatus, ScriptTarget

CHECKS = {"body", "order", "notes", "structure"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if sys.version_info >= (3, 11) else hashlib.sha256(stream.read()).hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def inspect_pdf(pdf):
    rows = []
    with fitz.open(pdf) as doc:
        if doc.needs_pass:
            raise ValueError("PDF 有密码保护，请提供你有权处理的解密副本。")
        if not doc.page_count:
            raise ValueError("PDF 没有页面。")
        for number, page in enumerate(doc, 1):
            text = page.get_text("text", sort=False)
            compact = "".join(text.split())
            images = page.get_image_info()
            image_area = sum(fitz.Rect(info["bbox"]).get_area() for info in images)
            invalid = text.count("\ufffd") / max(len(text), 1)
            drawings = bool(page.get_drawings())
            # A long footer is not evidence that a raster body was extracted.
            mostly_image = image_area > page.rect.get_area() * 0.4
            if compact and invalid < 0.02 and not mostly_image:
                route = "text"
            elif not compact and not images and not drawings:
                route = "blank"
            else:
                route = "ocr"
            rows.append({"page": number, "characters": len(compact), "route": route, "images": len(images), "replacement_ratio": round(invalid, 4)})
    return {"total_pages": len(rows), "pages": rows, "ocr_pages": [row["page"] for row in rows if row["route"] == "ocr"]}


def source_file(manifest):
    return manifest.get("source_file") or manifest["source_pdf"]


def review_source(bundle, manifest, number):
    if manifest.get("source_format") == "epub":
        return bundle / manifest["chapters"][number - 1]["source"]
    return bundle / f"review/pages/{number:04d}.png"


def review_evidence_digest(bundle, manifest, number):
    from .resources import image_snapshot
    path = review_source(bundle, manifest, number)
    draft = bundle / f"review/pages/{number:04d}.md"
    assets = image_snapshot(bundle, draft.read_text(encoding="utf-8")) if draft.exists() else {}
    if manifest.get("source_format") == "epub":
        assets.update(image_snapshot(bundle, path.read_text(encoding="utf-8"), base=path.parent))
    if path.exists():
        evidence = digest(path)
    else:
        from .housekeeping import archived_image_digest
        evidence = archived_image_digest(bundle, manifest, number)
        if not evidence:
            raise ValueError("来源页图缺失，请先 page-image 恢复后复核。")
    if not assets and manifest.get("source_format") != "epub":
        return evidence
    return hashlib.sha256(json.dumps([evidence, assets], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def review_evidence_available(bundle, manifest, number):
    if review_source(bundle, manifest, number).is_file():
        return True
    from .housekeeping import archived_image_digest
    return bool(archived_image_digest(bundle, manifest, number))


def inspect_material(pdf):
    if Path(pdf).suffix.lower() == ".epub":
        from .epub import inspect_epub
        return inspect_epub(pdf)
    if Path(pdf).suffix.lower() != ".pdf":
        raise ValueError("当前支持 PDF 和 EPUB，请提供这两种格式之一。")
    return {**inspect_pdf(pdf), "source_format": "pdf", "source_unit": "page"}


def engine(bundle, manifest):
    settings = replace(
        get_textferry_settings(),
        data_dir=bundle / "_work",
        ocr_provider=manifest["provider"],
        preserve_source_text=True,
        enable_reader_docx=False,
        enable_model_denoise=False,
        enable_agent_footnote_resolution=False,
        enable_flow_model_polish=False,
    )
    return TextFerryService(settings=settings)


def resolve_bundle(bundle):
    bundle = Path(bundle).expanduser().resolve()
    if not (bundle / "manifest.json").is_file() and (bundle / ".textferry/manifest.json").is_file():
        bundle = bundle / ".textferry"
    return bundle


def read_manifest(bundle):
    bundle = resolve_bundle(bundle)
    data = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    source = (bundle / source_file(data)).resolve()
    if not source.is_relative_to(bundle) or digest(source) != data["source_sha256"]:
        raise ValueError("保存的来源文件已改变或位于任务目录之外，需重新转换以保证校核对应正确底本。")
    if data.get("original_source_file"):
        original = (bundle / data["original_source_file"]).resolve()
        if not original.is_relative_to(bundle) or digest(original) != data["original_source_sha256"]:
            raise ValueError("保存的完整来源文件已改变，请先恢复对应底本。")
    return bundle, data


def convert(pdf, output, *, mode="quick", kind="paper", method="auto", provider="baidu_async", model=None, script="original", output_format="markdown", pages=None, chapters=None, keep_original=False):
    pdf, output = Path(pdf).expanduser().resolve(), Path(output).expanduser().resolve()
    if mode not in {"quick", "review"} or kind not in {"paper", "book", "classical"} or method not in {"auto", "text", "ocr"}:
        raise ValueError("不支持的材料类型、转换方式或档位。")
    if output_format not in {"markdown", "word", "both"}:
        raise ValueError("请选择 Markdown 或 Word 输出格式。")
    if pdf.suffix.lower() == ".epub":
        if pages is not None:
            raise ValueError("EPUB 请按章名选择章节范围，不能使用 PDF 物理页范围。")
        from .epub import convert_epub
        return convert_epub(pdf, output, mode=mode, kind=kind, method=method, provider=provider, model=model, script=script, output_format=output_format, chapters=chapters)
    if chapters is not None:
        raise ValueError("PDF 请用页段范围，EPUB 才使用章节范围。")
    from .selection import parse_selection
    diagnosis = inspect_material(pdf)
    selected = parse_selection(pages, diagnosis["total_pages"])
    original_pdf = pdf
    original_sha = digest(pdf)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("输出位置已有内容，请选择新目录；继续旧任务请使用 resume。")
    if method == "text" and set(diagnosis["ocr_pages"]) & set(selected):
        raise ValueError("部分页面没有可靠文本层，请使用自动处理或 OCR，以免把缺字页面当作完整全文。")
    output.mkdir(parents=True, exist_ok=True)
    temporary_selection = None
    if keep_original and pages is not None:
        import shutil
        original = output / "source/input-original.pdf"
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, original)
    if selected != list(range(1, diagnosis['total_pages'] + 1)):
        from .pdf_render import export_pdf_pages
        pdf = output / "source/selected.pdf"
        export_pdf_pages(original_pdf, selected, pdf)
        temporary_selection = pdf
        diagnosis = inspect_material(pdf)
    manifest = {
        "schema_version": 1, "source_format": "pdf", "source_unit": "page", "mode": mode, "kind": kind, "method": method, "output_format": output_format,
        "provider": provider, "script": script, "source_name": original_pdf.name,
        "source_page_map": selected, "original_source_sha256": original_sha,
        "word_equations": os.getenv("TEXTFERRY_WORD_EQUATIONS", "native"),
        "source_sha256": digest(pdf), "total_pages": diagnosis["total_pages"],
        "diagnosis": diagnosis, "simulation": is_mock_ocr_enabled(),
    }
    if keep_original and pages is not None:
        manifest.update(original_source_file="source/input-original.pdf", original_source_sha256=original_sha)
    service = engine(output, manifest)
    created = service.create_job_from_pdf_path(pdf_path=pdf, page_mode=PageMode.PDF, script_target=ScriptTarget(script), model_option=model or ("paddleocr_vl_16" if provider == "baidu_async" else "deepseek_ocr"))
    manifest["job_id"] = created.job_id
    manifest["source_pdf"] = service.store.input_pdf_path(created.job_id).relative_to(output).as_posix()
    write_json(output / "manifest.json", manifest)
    if temporary_selection:
        temporary_selection.unlink()
        if not any(temporary_selection.parent.iterdir()):
            temporary_selection.parent.rmdir()
    with fitz.open(output / manifest["source_pdf"]) as doc:
        for row in diagnosis["pages"]:
            if method == "ocr" or row["route"] == "ocr":
                continue
            number = row["page"]
            page = doc[number - 1]
            from .progress import emit
            emit("pdf_extract", "正在提取 PDF 内容", current=number, total=manifest["total_pages"])
            raw = page.get_text("text", sort=False)
            from .pdf_assets import page_text_with_figures
            ordered, figures = page_text_with_figures(page, output, number)
            manifest.setdefault("figures", {})[str(number)] = figures
            normalized = normalize_ocr_text(ordered, ScriptTarget(script), preserve_content=True)
            service.store.mark_page_done(created.job_id, number, image_path=None, raw_text=raw, normalized_text=normalized, detected_raw=None, detected_norm=None, retry_count=0)
    write_json(output / "manifest.json", manifest)
    return resume(output)


def resume(bundle, *, retry_failed=False):
    bundle, manifest = read_manifest(bundle)
    if manifest.get("source_format") == "epub":
        from .epub import resume_epub
        return resume_epub(bundle, manifest)
    service = engine(bundle, manifest)
    job_id = manifest["job_id"]
    if retry_failed:
        service.retry_failed(job_id)
    pending = service.store.list_pending_pages(job_id)
    if pending:
        if not is_mock_ocr_enabled() and not load_api_key(manifest["provider"]):
            return export_bundle(bundle, manifest, service)
        try:
            service.run_job(job_id, provider=manifest["provider"], progress=lambda event: print(f"{event.get('event')} page={event.get('page', '')}", file=sys.stderr, flush=True) if event.get("event") in {"page_done", "page_failed", "job_provider_unavailable"} else None)
        except KeyboardInterrupt:
            export_bundle(bundle, manifest, service)
            raise
    return export_bundle(bundle, manifest, service)


def collect_report(bundle, manifest, service):
    rows = service.store.list_pages(manifest["job_id"])
    ledger_path = bundle / "review/ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"entries": []}
    latest = {item["page"]: item for item in ledger["entries"]}
    reviewed, partial, stale, unresolved = [], [], [], []
    for row in rows:
        number = row["pdfPageNo"]
        entry = latest.get(number)
        if not entry:
            continue
        text = bundle / f"review/pages/{number:04d}.md"
        image = review_source(bundle, manifest, number)
        normalized = service.store.page_normalized_path(manifest["job_id"], number)
        current = text.exists() and review_evidence_available(bundle, manifest, number) and normalized.exists() and entry["text_sha256"] == digest(text) and entry["image_sha256"] == review_evidence_digest(bundle, manifest, number) and entry["machine_sha256"] == digest(normalized) and entry["source_sha256"] == manifest["source_sha256"] and row["status"] == PageStatus.DONE.value
        if not current:
            stale.append(number)
        elif CHECKS.issubset(entry["checks"]):
            reviewed.append(number)
            if entry["issues"]:
                unresolved.append(number)
        else:
            partial.append(number)
    done = [row["pdfPageNo"] for row in rows if row["status"] == PageStatus.DONE.value]
    failed = [row["pdfPageNo"] for row in rows if row["status"] == PageStatus.FAILED.value]
    pending = [row["pdfPageNo"] for row in rows if row["status"] in {PageStatus.PENDING.value, PageStatus.PROCESSING.value}]
    unreviewed = [row["pdfPageNo"] for row in rows if row["pdfPageNo"] not in reviewed]
    status = "partial" if failed or pending else "machine_ready"
    if manifest.get("image_only_chapters") and manifest["mode"] == "quick":
        status = "partial"
    if manifest["mode"] == "review" and not failed and not pending:
        status = "awaiting_review" if unreviewed else ("reviewed_with_issues" if unresolved else "reviewed")
    result = {
        "schema_version": 1, "status": status, "mode": manifest["mode"], "kind": manifest["kind"],
        "output_format": manifest.get("output_format", "markdown"), "job_id": manifest["job_id"], "total_pages": manifest["total_pages"], "processed_pages": len(done),
        "failed_pages": failed, "pending_pages": pending, "reviewed_pages": reviewed,
        "unreviewed_pages": unreviewed, "partially_checked_pages": partial, "stale_review_pages": stale,
        "unresolved_pages": unresolved, "source_pdf": manifest.get("source_pdf"), "source_file": source_file(manifest),
        "source_format": manifest.get("source_format", "pdf"), "source_unit": manifest.get("source_unit", "page"), "source_page_map": manifest.get("source_page_map"), "source_chapter_map": manifest.get("source_chapter_map"), "source_sha256": manifest["source_sha256"],
        "simulation": manifest["simulation"], "review_records": ledger["entries"],
        "origin": manifest.get("origin", "local"),
        "shared_id": manifest.get("shared_id"), "image_only_chapters": manifest.get("image_only_chapters", []), "warnings": manifest.get("warnings", []),
        "upstream_review_scope": manifest.get("upstream_review_scope"),
        "artifacts": ["machine.md", "report.json", "report.md"] + (["document.review.md", "review/pages/"] if manifest["mode"] == "review" else []),
    }
    if pending and not is_mock_ocr_enabled() and not load_api_key(manifest["provider"]):
        result["status"] = "needs_setup"
        result["onboarding"] = onboarding() if manifest["provider"] == "baidu_async" else {"provider": "siliconflow", "message": "请配置所选服务的访问密钥。"}
    from .handwash import handwash_status
    result["handwash"] = handwash_status(bundle)
    if result["handwash"]["current"]:
        result["artifacts"].extend(["handwashed.md", "handwashed.sources.json", "整理说明.md"])
        if result["handwash"].get("output_format") in {"word", "both"} and (bundle / "整理稿.docx").exists():
            result["artifacts"].append("整理稿.docx")
    if manifest["mode"] == "quick" and (bundle / "基础稿.docx").exists():
        result["artifacts"].append("基础稿.docx")
    return result


def export_bundle(bundle, manifest, service):
    rows = service.store.list_pages(manifest["job_id"])
    if all(row["status"] == PageStatus.DONE.value for row in rows):
        service.store.set_job_status(manifest["job_id"], JobStatus.FINAL_READY)
    service._refresh_job_summary(manifest["job_id"])
    machine = [f"# {manifest['source_name']}", "", "> 机器转换底稿；转换完成不代表已逐页校核。", ""]
    reviewed_document = [f"# {manifest['source_name']}", "", "> 校核工作稿；实际核查范围见 report.md 和 review/ledger.json。", ""]
    for row in rows:
        number = row["pdfPageNo"]
        epub = manifest.get("source_format") == "epub"
        source_number = manifest.get("source_chapter_map" if epub else "source_page_map", list(range(1, manifest["total_pages"] + 1)))[number - 1]
        title = f"## EPUB 第 {source_number} 节 | SRC:c{number:04d}" if epub else f"## Page {number} | PDF页:{source_number} | SRC:p{number:04d}"
        source_link = f"[来源章节]({manifest['chapters'][number - 1]['source']})" if epub else f"[来源页]({manifest.get('original_source_file', manifest['source_pdf'])}#page={source_number if manifest.get('original_source_file') else number})"
        normalized = service.store.page_normalized_path(manifest["job_id"], number)
        text = normalized.read_text(encoding="utf-8") if row["status"] == PageStatus.DONE.value and normalized.exists() else f"【此页尚未完成转换；状态：{row['status']}】"
        machine.extend([title, "", source_link, "", text, ""])
        if manifest["mode"] == "review":
            page_dir = bundle / "review/pages"
            page_dir.mkdir(parents=True, exist_ok=True)
            draft = page_dir / f"{number:04d}.md"
            image = page_dir / f"{number:04d}.png"
            if not draft.exists() and row["status"] == PageStatus.DONE.value:
                draft.write_text(text, encoding="utf-8")
            if not epub and not review_evidence_available(bundle, manifest, number):
                render_pdf_page_png(bundle / manifest["source_pdf"], number, image, dpi=120)
            reviewed_document.extend([title, "", source_link, "", draft.read_text(encoding="utf-8") if draft.exists() else text, ""])
    target, mark = bundle / "machine.md", bundle / "machine-export.json"
    generated = "\n".join(machine)
    old = json.loads(mark.read_text(encoding="utf-8")) if mark.exists() else {}
    modified = target.exists() and (digest(target) != old["sha256"] if old.get("sha256") else target.read_text(encoding="utf-8") != generated)
    if not modified:
        target.write_text(generated, encoding="utf-8")
        write_json(mark, {"sha256": digest(target)})
    if not modified and manifest["mode"] == "quick" and manifest.get("output_format") in {"word", "both"} and all(row["status"] == PageStatus.DONE.value for row in rows):
        from .word_export import write_word
        candidate = bundle / "基础稿.docx"
        mark = bundle / "basic-word.json"
        old = json.loads(mark.read_text(encoding="utf-8")) if mark.exists() else {}
        if candidate.exists() and old.get("sha256") != digest(candidate):
            raise ValueError("现有 Word 基础稿已被修改，不会直接覆盖。")
        if old.get("machine_sha256") != digest(bundle / "machine.md") or not candidate.exists():
            readable = "\n".join(line for line in machine if not line.startswith(("> 机器转换底稿", "[来源页]", "[来源章节]")))
            write_word(readable, candidate, bundle, equations=manifest.get("word_equations"))
            write_json(mark, {"sha256": digest(candidate), "machine_sha256": digest(bundle / "machine.md")})
    if manifest["mode"] == "review":
        (bundle / "document.review.md").write_text("\n".join(reviewed_document), encoding="utf-8")
    report = collect_report(bundle, manifest, service)
    report["machine_user_modified"] = modified
    if modified:
        report["warnings"].append("机器底稿有直接修改，已保留；原始识别文字仍保存在任务记录中。")
    write_json(bundle / "report.json", report)
    lines = ["# 材料处理报告", "", f"- 状态：{report['status']}", f"- 档位：{report['mode']}", f"- 总页数：{report['total_pages']}；转换完成：{report['processed_pages']}", f"- 已对照正文、顺序、脚注和层次的 PDF 页：{report['reviewed_pages']}", f"- 未完成全部核查的 PDF 页：{report['unreviewed_pages']}", f"- 仅部分核查的 PDF 页：{report['partially_checked_pages']}", f"- 校核后内容有变化、需重核的 PDF 页：{report['stale_review_pages']}", f"- 已核仍有待核问题的 PDF 页：{report['unresolved_pages']}", f"- 转换失败页：{report['failed_pages']}；等待处理页：{report['pending_pages']}", f"- 模拟识别：{report['simulation']}", "", "核查记录由执行者提交并与文件哈希关联，不构成独立质量认证。", ""]
    lines.extend(["## 手洗成稿", "", f"状态：{report['handwash']['status']}；与当前工作稿一致：{report['handwash']['current']}。", "", "machine.md 为机器底稿；document.review.md 为工作稿汇总。仅完成手洗导出后才生成 handwashed.md，学术准出另按材料 SOP 验收。", ""])
    for entry in report["review_records"]:
        lines.extend([f"## PDF 第 {entry['page']} 页的核查记录", "", f"核查者：{entry['reviewer']}；项目：{', '.join(entry['checks'])}", "", entry["note"], "", f"待核：{entry['issues']}", ""])
    human_report = "\n".join(lines)
    if manifest.get("source_format") == "epub":
        human_report = human_report.replace("PDF 第 ", "EPUB 第 ").replace("PDF 页", "EPUB 节").replace("总页数", "总章节数").replace(" 页的核查", " 节的核查").replace("转换失败页", "转换失败章节").replace("等待处理页", "等待处理章节")
    (bundle / "report.md").write_text(human_report, encoding="utf-8")
    return report


def review_page(bundle, page, *, checks, reviewer, note, issues):
    bundle, manifest = read_manifest(bundle)
    if manifest["mode"] != "review" or not 1 <= page <= manifest["total_pages"]:
        raise ValueError("请在校核整理任务中选择有效的 PDF 页。")
    check_set = {item.strip() for item in checks.split(",") if item.strip()}
    if not check_set or not check_set.issubset(CHECKS) or not reviewer.strip() or not note.strip():
        raise ValueError("请说明实际核查项目、核查者和具体核查记录。")
    service = engine(bundle, manifest)
    if service.store.get_page(manifest["job_id"], page)["status"] != PageStatus.DONE.value:
        raise ValueError("此页尚未完成转换，请先恢复任务。")
    draft, image = bundle / f"review/pages/{page:04d}.md", review_source(bundle, manifest, page)
    if manifest.get('source_format') != 'epub' and not image.exists():
        from .housekeeping import page_image
        page_image(bundle, page=page)
    from .resources import image_snapshot
    if any(value is None for value in image_snapshot(bundle, draft.read_text(encoding="utf-8")).values()):
        raise ValueError("工作稿中的图片缺失或不是本地资源，请先补齐后再记录核查。")
    entry = {
        "page": page, "checks": sorted(check_set), "reviewer": reviewer.strip(), "note": note.strip(), "issues": issues,
        "source_sha256": manifest["source_sha256"], "text_sha256": digest(draft), "image_sha256": review_evidence_digest(bundle, manifest, page),
        "machine_sha256": digest(service.store.page_normalized_path(manifest["job_id"], page)),
    }
    path = bundle / "review/ledger.json"
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": []}
    ledger["entries"].append(entry)
    write_json(path, ledger)
    return export_bundle(bundle, manifest, service)


def upgrade_review(bundle):
    bundle, manifest = read_manifest(bundle)
    if manifest["mode"] == "quick":
        mark = bundle / "machine-export.json"
        if mark.exists() and digest(bundle / "machine.md") != json.loads(mark.read_text())["sha256"]:
            raise ValueError("底稿已有直接修订，已保留。请让 Agent 先把这些修改对应到来源单元，再升级整理。")
        manifest["mode"] = "review"
        write_json(bundle / "manifest.json", manifest)
    return export_bundle(bundle, manifest, engine(bundle, manifest))


def change_output(bundle, *, output_format, equations=None):
    if output_format not in {"markdown", "word", "both"} or equations not in {None, "native", "compatible"}:
        raise ValueError("请选择 Markdown / Word 格式，以及有效的公式方式。")
    bundle, manifest = read_manifest(bundle)
    from .delivery import check_user_edits
    check_user_edits(bundle)
    manifest["output_format"] = output_format
    if equations is not None:
        manifest["word_equations"] = equations
    if manifest["mode"] == "quick" and output_format in {"word", "both"}:
        from .word_export import write_word
        target, marker = bundle / "基础稿.docx", bundle / "basic-word.json"
        saved = json.loads(marker.read_text()) if marker.exists() else {}
        if target.exists() and saved.get("sha256") != digest(target):
            raise ValueError("Word 基础稿已有直接修订，已保留；请先另存修订后再追加格式。")
        service = engine(bundle, manifest)
        if any(row["status"] != PageStatus.DONE.value for row in service.store.list_pages(manifest["job_id"])):
            raise ValueError("还有内容未转换完成，请先继续原任务，再追加 Word。")
        text = (bundle / "machine.md").read_text(encoding="utf-8")
        readable = "\n".join(line for line in text.splitlines() if not line.startswith(("> 机器转换底稿", "[来源页]", "[来源章节]")))
        write_word(readable, target, bundle, equations=manifest.get("word_equations"))
        write_json(marker, {"sha256": digest(target), "machine_sha256": digest(bundle / "machine.md")})
    write_json(bundle / "manifest.json", manifest)
    state = bundle / "handwash/export.json"
    if manifest["mode"] == "review" and state.exists():
        from .handwash import export_handwash
        saved = json.loads(state.read_text())
        mapping = json.loads((bundle / "handwashed.sources.json").read_text())
        export_handwash(bundle, reviewer=mapping["reviewer"], note=mapping["assembly_note"], page_numbers=saved["page_numbers"], allow_unresolved=saved["status"] == "handwashed_with_issues", output_format=output_format)
    return export_bundle(bundle, manifest, engine(bundle, manifest))


def task_status(bundle):
    bundle, manifest = read_manifest(bundle)
    report = collect_report(bundle, manifest, engine(bundle, manifest))
    if report['status'] == 'needs_setup':
        stage, message = 'needs_setup', '需要在执行环境配置访问令牌。'
    elif report['failed_pages']:
        stage, message = 'partial', '有页面未完成，已保留结果和恢复记录。'
    elif report['pending_pages']:
        stage, message = 'pending', '还有页面等待结果或待继续；当前阶段以运行输出为准。'
    elif manifest['mode'] == 'review' and not report['handwash']['current']:
        stage, message = 'review', '文字提取完成，正在等待对照核查或导出当前整理稿。'
    else:
        stage, message = 'ready', '本次处理结果已准备好。'
    return {'status': report['status'], 'stage': stage, 'message': message,
            'processed': report['processed_pages'], 'total': report['total_pages'],
            'reviewed': len(report['reviewed_pages']), 'failed': report['failed_pages'],
            'pending': report['pending_pages'], 'handwash_current': report['handwash']['current']}


def parser():
    root = argparse.ArgumentParser(description="中文材料转 Markdown 与按范围校核")
    sub = root.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="打开首次使用引导，检查本机配置")
    setup.add_argument("--prepare", action="store_true", help="创建可由用户填写的本机配置文件")
    configure = sub.add_parser("configure", help="在本机保存访问令牌，不输出令牌")
    configure.add_argument("--key-file", type=Path)
    configure.add_argument("--provider", choices=["baidu_async", "siliconflow"], default="baidu_async")
    inspect = sub.add_parser("inspect", help="检查 PDF 文本层或 EPUB 章节和资源")
    inspect.add_argument("pdf", type=Path)
    convert_parser = sub.add_parser("convert", help="转换材料")
    convert_parser.add_argument("pdf", type=Path)
    convert_parser.add_argument("--output", required=True, type=Path)
    convert_parser.add_argument("--mode", choices=["quick", "review"], default="quick")
    convert_parser.add_argument("--kind", choices=["paper", "book", "classical"], default="paper")
    convert_parser.add_argument("--method", choices=["auto", "text", "ocr"], default="auto")
    convert_parser.add_argument("--provider", choices=["baidu_async", "siliconflow"], default="baidu_async")
    convert_parser.add_argument("--model")
    convert_parser.add_argument("--pages", help="PDF 原文件物理页，例如 1-10,13")
    convert_parser.add_argument("--chapters", help="EPUB 原章节单元，例如 2-5")
    convert_parser.add_argument("--keep-original", action="store_true", help="选页任务额外保留整本 PDF；默认只保存所选页")
    convert_parser.add_argument("--format", dest="output_format", choices=["markdown", "word", "both"], default="markdown")
    convert_parser.add_argument("--script", choices=["original", "simp", "trad"], default="original")
    resume_parser = sub.add_parser("resume", help="继续旧任务")
    resume_parser.add_argument("bundle", type=Path)
    resume_parser.add_argument("--retry-failed", action="store_true")
    for command in ("upgrade-review", "change-output"):
        option = sub.add_parser(command)
        option.add_argument("bundle", type=Path)
        if command == "change-output":
            option.add_argument("--format", dest="output_format", choices=["markdown", "word", "both"], required=True)
            option.add_argument("--equations", choices=["native", "compatible"])
    sub.add_parser("doctor", help="验证运行环境与离线自制样本，不调用 OCR")
    report = sub.add_parser("report", help="重算核查范围并更新报告")
    report.add_argument("bundle", type=Path)
    status_parser = sub.add_parser('status', help='简短查看完成数和待处理阶段，不展开诊断记录')
    status_parser.add_argument('bundle', type=Path)
    deliver_parser = sub.add_parser('deliver', help='生成简洁的用户成品目录，保留回查链接')
    deliver_parser.add_argument('bundle', type=Path)
    compact_parser = sub.add_parser('compact', help='预览已完成任务中可清理的缓存；--apply 执行')
    compact_parser.add_argument('bundle', type=Path)
    compact_parser.add_argument('--apply', action='store_true')
    image_parser = sub.add_parser('page-image', help='按需恢复一页 PDF 校核图，不调用 OCR')
    image_parser.add_argument('bundle', type=Path)
    image_parser.add_argument('--page', type=int, required=True)
    review = sub.add_parser("review-page", help="记录实际完成的单页核查")
    review.add_argument("bundle", type=Path)
    review.add_argument("--page", required=True, type=int)
    review.add_argument("--checks", required=True)
    review.add_argument("--reviewer", required=True)
    review_note = review.add_mutually_exclusive_group(required=True)
    review_note.add_argument("--note")
    review_note.add_argument("--note-file", type=Path, help='从 UTF-8 文件读取核查说明，避免公式或引号被命令行解释')
    review.add_argument("--issue", action="append", default=[])
    for name in ("prepare-handwash", "handwash-status", "export-handwash"):
        handwash = sub.add_parser(name)
        handwash.add_argument("bundle", type=Path)
        if name == "export-handwash":
            handwash.add_argument("--page-numbers", choices=["hide", "show"], default="hide")
            handwash.add_argument("--reviewer", required=True)
            handwash_note = handwash.add_mutually_exclusive_group(required=True)
            handwash_note.add_argument("--note")
            handwash_note.add_argument("--note-file", type=Path)
            handwash.add_argument("--allow-unresolved", action="store_true")
            handwash.add_argument("--format", dest="output_format", choices=["markdown", "word", "both"])
    find = sub.add_parser("find-shared", help="查找已审核的共享成品")
    find.add_argument("pdf", nargs="?", type=Path)
    find.add_argument("--catalog")
    find.add_argument("--title")
    find.add_argument("--script", choices=["original", "simp", "trad"], default="original")
    reuse = sub.add_parser("reuse-shared", help="校验并复用成品，不调用 OCR")
    reuse.add_argument("pdf", type=Path)
    reuse.add_argument("--catalog")
    reuse.add_argument("--entry-id", dest="identifier", required=True)
    reuse.add_argument("--output", type=Path, required=True)
    reuse.add_argument("--mode", choices=["quick", "review"], default="quick")
    reuse.add_argument("--script", choices=["original", "simp", "trad"], default="original")
    download = sub.add_parser("download-shared", help="没有本地 PDF 时取得已审核的 Markdown")
    download.add_argument("--catalog")
    download.add_argument("--entry-id", dest="identifier", required=True)
    download.add_argument("--output", type=Path, required=True)
    prepare = sub.add_parser("prepare-share", help="准备不含原 PDF 或私人记录的本地贡献包")
    prepare.add_argument("bundle", type=Path)
    prepare.add_argument("--rights-file", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    for name in ("share-status", "record-share-decision"):
        share = sub.add_parser(name)
        share.add_argument("bundle", type=Path)
        share.add_argument("--candidate", type=Path, required=True)
        share.add_argument("--repository", help="本次公开目标 owner/repository")
        if name == "record-share-decision":
            share.add_argument("--decision", choices=["yes", "no"], required=True)
    return root


def main(argv=None):
    args = vars(parser().parse_args(argv))
    command = args.pop("command")
    try:
        if 'note_file' in args:
            note_file = args.pop('note_file')
            if note_file is not None:
                args['note'] = note_file.read_text(encoding='utf-8')
        if command == "setup":
            result = onboarding(**args)
        elif command == "configure":
            key_file = args.pop("key_file")
            key = key_file.read_text(encoding="utf-8") if key_file else getpass.getpass("粘贴 Access Token（输入不会显示）：")
            result = save_api_key(key, **args)
        elif command == "inspect":
            result = inspect_material(**args)
        elif command == "convert":
            result = convert(**args)
        elif command == "resume":
            result = resume(**args)
        elif command == "doctor":
            from .diagnostics import doctor
            result = doctor()
        elif command == 'deliver':
            from .delivery import deliver
            result = deliver(**args)
        elif command == 'status':
            result = task_status(**args)
        elif command in {'compact', 'page-image'}:
            from .housekeeping import compact, page_image
            result = (compact if command == 'compact' else page_image)(**args)
        elif command == "upgrade-review":
            result = upgrade_review(**args)
        elif command == "change-output":
            result = change_output(**args)
        elif command == "review-page":
            args["issues"] = args.pop("issue")
            result = review_page(**args)
        elif command in {"prepare-handwash", "handwash-status", "export-handwash"}:
            from . import handwash
            result = {"prepare-handwash": handwash.prepare_handwash, "handwash-status": handwash.handwash_status, "export-handwash": handwash.export_handwash}[command](**args)
        elif command in {"find-shared", "download-shared", "reuse-shared", "prepare-share", "share-status", "record-share-decision"}:
            from . import sharing
            handler = {"find-shared": sharing.find_shared, "download-shared": sharing.download_shared, "reuse-shared": sharing.reuse_shared, "prepare-share": sharing.prepare_share, "share-status": sharing.share_status, "record-share-decision": sharing.record_share_decision}[command]
            result = handler(**args)
        else:
            bundle, manifest = read_manifest(args["bundle"])
            result = export_bundle(bundle, manifest, engine(bundle, manifest))
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get("status") == "needs_setup" else (3 if result.get("status") == "partial" else 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        message = str(exc)
        for provider in ("baidu_async", "siliconflow"):
            try:
                key = load_api_key(provider)
                if key:
                    message = message.replace(key, "[已隐藏]")
            except ValueError:
                pass
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
