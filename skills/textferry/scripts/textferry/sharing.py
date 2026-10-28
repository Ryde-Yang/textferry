# SPDX-License-Identifier: AGPL-3.0-only
"""Prepare consent-bound text contributions and reuse approved GitHub records."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import httpx

from .credentials import load_api_key
from .types import PageMode, PageStatus, ScriptTarget
from .workflow import digest, engine, export_bundle, read_manifest, write_json

MAX_TEXT_BYTES = 20 * 1024 * 1024
HOME_PATH = re.compile(r"/Users/[^/\s]+/|/home/[^/\s]+/|/private/var/|/var/folders/|[A-Za-z]:\\Users\\")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def privacy_check(text):
    if HOME_PATH.search(text):
        raise ValueError("共享正文或元数据含本机路径，请在本地检查并删除私人信息后重建贡献包。")
    keys = [os.getenv(name, "") for name in ("PADDLEOCR_ACCESS_TOKEN", "BAIDU_API_KEY", "SILICONFLOW_API_KEY")]
    keys.extend(load_api_key(provider) for provider in ("baidu_async", "siliconflow"))
    for key in keys:
        if key and len(key) > 8 and key in text:
            raise ValueError("共享内容中检测到已配置的凭证，请移除后重建。")


def validate_rights(data):
    rights = data.get("source_rights", {})
    if rights.get("basis") not in {"public_domain", "permission"}:
        raise ValueError("共享需明确权利依据：公版核实或权利人授权；来源不明的全文不能进入贡献包。")
    if not rights.get("jurisdiction") or not rights.get("statement") or not rights.get("evidence"):
        raise ValueError("请补充权利适用地区、说明和可供审核的依据。用户愿意分享本身不代替原书授权。")
    if not isinstance(rights["evidence"], list) or not all(isinstance(item, str) and item.strip() for item in rights["evidence"]):
        raise ValueError("权利依据应为非空说明或公开来源列表。")
    if not data.get("title") or not data.get("edition"):
        raise ValueError("请明确书名和版本，不能仅凭同名书复用。")
    if not data.get("scope_description"):
        raise ValueError("请说明成品范围，是全书、单卷、章节还是选页；完整输入 PDF 不自动等于完整图书。")
    if data.get("contribution_license") not in {"CC0-1.0", "CC-BY-4.0"}:
        raise ValueError("请为贡献者拥有的新增内容选择 CC0-1.0 或 CC-BY-4.0；程序 AGPL 不自动授权书籍全文。")
    if data["contribution_license"] == "CC-BY-4.0" and not data.get("attribution"):
        raise ValueError("CC-BY-4.0 贡献需要贡献者主动指定公开署名。")
    clean = {key: data[key] for key in ("title", "edition", "scope_description", "contribution_license", "attribution") if key in data}
    clean["source_rights"] = {key: rights[key] for key in ("basis", "jurisdiction", "statement", "evidence")}
    return clean


def ensure_text_only(raw):
    if re.search(r'!\[|<\s*(?:img|svg|image|object|video|audio)\b', raw.decode("utf-8"), re.I):
        raise ValueError("当前共享目录仅支持纯文字，含图片或媒体的成品请保留本地；不会生成或下载丢失图片的成品包。")


def prepare_share(bundle, rights_file, output):
    bundle, source = read_manifest(bundle)
    if source.get("source_format") == "epub":
        raise ValueError("EPUB 成果目前仅支持本地保存，尚未接入共享目录；不会伪装成 PDF 贡献。")
    service = engine(bundle, source)
    report = export_bundle(bundle, source, service)
    if report["pending_pages"] or report["failed_pages"] or report["simulation"]:
        raise ValueError("请先完成真实转换；模拟识别或仍有缺页的任务暂不准备完整共享成品。")
    if report["handwash"]["status"] == "stale":
        raise ValueError("手洗成稿已失效，请回写、复核并重新导出后再准备贡献包。")
    rights = validate_rights(json.loads(Path(rights_file).read_text(encoding="utf-8")))
    output = Path(output).expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("共享草稿目录已有内容，请使用新目录。")
    content = bytearray()
    pages = []
    for row in service.store.list_pages(source["job_id"]):
        number = row["pdfPageNo"]
        draft = bundle / f"review/pages/{number:04d}.md"
        path = draft if source["mode"] == "review" and draft.exists() else service.store.page_normalized_path(source["job_id"], number)
        text = path.read_text(encoding="utf-8")
        header = f"## PDF 第 {number} 页\n\n".encode()
        content.extend(header)
        start = len(content)
        encoded = text.encode("utf-8")
        content.extend(encoded)
        pages.append({"pdf_page": number, "start": start, "end": len(content), "text_sha256": sha(encoded)})
        content.extend(b"\n\n")
    if len(content) > MAX_TEXT_BYTES:
        raise ValueError("共享全文超过本工具单份 20 MiB 上限，请按有来源映射的卷章拆分贡献。")
    quality = "machine" if source["mode"] == "quick" else ("reviewed_with_issues" if not report["unreviewed_pages"] and report["unresolved_pages"] else ("reviewed" if not report["unreviewed_pages"] else "partly_reviewed"))
    metadata = {
        "schema_version": 1, **rights, "source_pdf_sha256": source["source_sha256"], "kind": source["kind"],
        "total_pages": source["total_pages"], "coverage": "complete_input_pdf", "script_target": source["script"],
        "quality": quality, "rights_review": "contributor_statement_unverified",
        "review_scope": {key: report[key] for key in ("reviewed_pages", "partially_checked_pages", "stale_review_pages", "unresolved_pages")},
        "license_scope": "贡献许可仅适用于贡献者实际拥有的新增权利；底本文字权利按 source_rights 逐项说明。",
        "content": "content.md", "content_sha256": sha(content), "pages": pages,
    }
    additional = {}
    if report["handwash"]["current"]:
        machine = bytearray()
        machine_pages = []
        for row in service.store.list_pages(source["job_id"]):
            number = row["pdfPageNo"]
            machine.extend(f"## PDF 第 {number} 页\n\n".encode())
            start = len(machine)
            raw = service.store.page_normalized_path(source["job_id"], number).read_bytes()
            machine.extend(raw)
            machine_pages.append({"pdf_page": number, "start": start, "end": len(machine), "text_sha256": sha(raw)})
            machine.extend(b"\n\n")
        additional = {"machine.md": bytes(machine), "handwashed.md": (bundle / "handwashed.md").read_bytes()}
        if any(len(raw) > MAX_TEXT_BYTES for raw in additional.values()):
            raise ValueError("共享文字超过单份 20 MiB 上限。")
        metadata["additional_texts"] = {name: sha(raw) for name, raw in additional.items()}
        metadata["machine_pages"] = machine_pages
        metadata["handwash"] = {key: report["handwash"][key] for key in ("status", "covered_pages", "page_numbers", "academic_clearance")}
    # Text-only catalog: refuse assets rather than produce broken shared copies.
    for raw in [bytes(content), *additional.values()]:
        ensure_text_only(raw)
    identifier = sha(json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode())[:24]
    metadata["id"] = identifier
    preview = json.dumps(metadata, ensure_ascii=False) + content.decode("utf-8")
    privacy_check(preview + "\n".join(raw.decode("utf-8") for raw in additional.values()))
    output.mkdir(parents=True, exist_ok=True)
    (output / "content.md").write_bytes(content)
    for name, raw in additional.items():
        (output / name).write_bytes(raw)
    write_json(output / "manifest.json", metadata)
    (output / "README.md").write_text(f"# {rights['title']}\n\n版本：{rights['edition']}\n\n覆盖：输入 PDF 共 {source['total_pages']} 页；质量状态：{quality}。\n\n权利依据：{rights['source_rights']['statement']}\n\n贡献许可：{rights['contribution_license']}（仅限贡献者拥有的权利）。\n\n当前为待维护者审核的贡献草稿，不含原 PDF、页图、账号、本机路径或诊断日志。\n", encoding="utf-8")
    if additional:
        with (output / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("\n主产物：machine.md（按页机器底稿）与 handwashed.md（手洗稿）。content.md 是保留逐页工作文字的复用中间件；手洗范围见 manifest.json。未包含私人来源映射和核查自由文字。\n")
    return {"id": identifier, "candidate": str(output), "publication_status": "local_draft", "quality": quality, "consent": share_status(bundle, output)}


def additional_names(metadata):
    names = metadata.get("additional_texts", {})
    if not isinstance(names, dict) or set(names) - {"machine.md", "handwashed.md"} or names and set(names) != {"machine.md", "handwashed.md"}:
        raise ValueError("共享附加产物名称不支持。")
    return sorted(names)


def candidate_fingerprint(candidate):
    candidate = Path(candidate)
    metadata = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    return sha(b"\0".join((candidate / name).read_bytes() for name in ["manifest.json", "content.md", "README.md"] + additional_names(metadata)))


def share_status(bundle, candidate, *, repository=None):
    bundle, source = read_manifest(bundle)
    candidate = Path(candidate)
    metadata = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    if metadata["source_pdf_sha256"] != source["source_sha256"]:
        raise ValueError("贡献包来源与当前任务不一致，不能借用其他材料的分享确认。")
    validate_rights(metadata)
    privacy_check("\n".join((candidate / name).read_text(encoding="utf-8") for name in ["manifest.json", "content.md", "README.md"] + additional_names(metadata)))
    canonical = {key: value for key, value in metadata.items() if key != "id"}
    valid_id = sha(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode())[:24] == metadata["id"]
    valid = valid_id and digest(candidate / "content.md") == metadata["content_sha256"]
    valid = valid and all(digest(candidate / name) == metadata["additional_texts"][name] for name in additional_names(metadata))
    fingerprint = candidate_fingerprint(candidate)
    path = bundle / "sharing-decisions.json"
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    latest = next((item for item in reversed(decisions) if item["fingerprint"] == fingerprint and (item.get("repository") == repository or (item["decision"] == "no" and item.get("repository") is None))), None)
    return {"id": metadata["id"], "fingerprint": fingerprint, "valid": valid, "repository": repository, "should_ask": bool(valid and repository and latest is None), "consented": bool(valid and repository and latest is not None and latest["decision"] == "yes"), "decision": latest["decision"] if latest else None}


def record_share_decision(bundle, candidate, decision, *, repository=None):
    if decision not in {"yes", "no"}:
        raise ValueError("仅接受用户明确同意或拒绝的记录。")
    if decision == "yes" and (not repository or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)):
        raise ValueError("公开同意必须对应一个明确的 GitHub 仓库（owner/repository）。")
    bundle, _ = read_manifest(bundle)
    status = share_status(bundle, candidate, repository=repository)
    if not status["valid"]:
        raise ValueError("草稿已变化，请重建并重新展示具体共享内容后确认。")
    path = bundle / "sharing-decisions.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    data.append({"id": status["id"], "fingerprint": status["fingerprint"], "repository": repository, "decision": decision, "recorded_at": datetime.now(timezone.utc).isoformat()})
    write_json(path, data)
    return share_status(bundle, candidate, repository=repository)


def read_location(location, limit=MAX_TEXT_BYTES):
    value = str(location)
    if value.startswith("https://"):
        url = urlsplit(value)
        if url.hostname != "raw.githubusercontent.com" or url.username or url.query or url.fragment:
            raise ValueError("线上共享仅接受 GitHub 原始文件地址，不发送访问令牌。")
        with httpx.stream("GET", value, timeout=30, follow_redirects=False) as response:
            response.raise_for_status()
            body = bytearray()
            for block in response.iter_bytes():
                body.extend(block)
                if len(body) > limit:
                    raise ValueError("共享文件超过读取上限。")
            return bytes(body)
    path = Path(value).expanduser().resolve()
    if path.stat().st_size > limit:
        raise ValueError("共享文件超过读取上限。")
    return path.read_bytes()


def child_location(parent, relative):
    path = PurePosixPath(relative)
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", relative) or path.is_absolute() or ".." in path.parts:
        raise ValueError("共享索引包含不安全的文件路径。")
    if str(parent).startswith("https://"):
        return urljoin(str(parent), relative)
    root = Path(parent).expanduser().resolve().parent
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("共享路径不能指向索引目录外部。")
    return target


def read_catalog(catalog):
    catalog = catalog or os.getenv("TEXTFERRY_CATALOG")
    if not catalog:
        return None, {"schema_version": 1, "entries": []}
    data = json.loads(read_location(catalog, limit=2 * 1024 * 1024))
    if data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError("共享索引格式不支持。")
    return catalog, data


def find_shared(pdf=None, catalog=None, *, title=None, script="original"):
    location, data = read_catalog(catalog)
    if pdf is None and not title:
        raise ValueError("请提供 PDF 或要查找的书名。")
    fingerprint = digest(Path(pdf)) if pdf is not None else None
    exact, titles = [], []
    normalized_title = re.sub(r"\s+", "", title or "")
    for entry in data["entries"]:
        if entry.get("status") != "approved":
            continue
        if fingerprint is not None and entry.get("source_pdf_sha256") == fingerprint and entry.get("script_target") == script:
            exact.append(entry)
        elif normalized_title and re.sub(r"\s+", "", entry.get("title", "")) == normalized_title:
            titles.append(entry)
    return {"status": "searched" if location else "catalog_unconfigured", "source_pdf_sha256": fingerprint, "exact_matches": exact, "title_candidates": titles, "message": "同名书只作候选；自动复用要求同一 PDF、完整页覆盖、脚本目标一致及校验通过。"}


def load_approved(catalog, identifier, *, include_additional=False):
    location, data = read_catalog(catalog)
    entry = next((row for row in data["entries"] if row.get("id") == identifier), None)
    if entry is None or entry.get("status") != "approved":
        raise ValueError("所选共享成品尚未通过目录维护者审核，或不存在。")
    manifest_location = child_location(location, entry["manifest"])
    raw_manifest = read_location(manifest_location, limit=2 * 1024 * 1024)
    if sha(raw_manifest) != entry["manifest_sha256"]:
        raise ValueError("共享清单校验失败。")
    metadata = json.loads(raw_manifest)
    for key in ("id", "source_pdf_sha256", "total_pages", "script_target", "quality"):
        if metadata[key] != entry[key]:
            raise ValueError("共享目录与成品清单的版本校验不一致。")
    validate_rights(metadata)
    raw_text = read_location(child_location(manifest_location, metadata["content"]))
    if sha(raw_text) != metadata["content_sha256"]:
        raise ValueError("共享正文校验失败。")
    additional = {}
    for name in additional_names(metadata):
        raw = read_location(child_location(manifest_location, name))
        if sha(raw) != metadata["additional_texts"][name]:
            raise ValueError("共享机器底稿或手洗稿校验失败。")
        raw.decode("utf-8")
        additional[name] = raw
    for raw in [raw_text, *additional.values()]:
        ensure_text_only(raw)
    return (metadata, raw_text, additional) if include_additional else (metadata, raw_text)


def validated_pages(metadata, content):
    pages, total = metadata["pages"], metadata["total_pages"]
    if metadata.get("schema_version") != 1 or not isinstance(total, int) or not 0 < total <= 10000 or len(pages) != total or metadata.get("coverage") != "complete_input_pdf":
        raise ValueError("共享清单格式或完整页覆盖不支持。")
    page_texts, previous_end = [], 0
    for number, row in enumerate(pages, 1):
        start, end = row["start"], row["end"]
        if row["pdf_page"] != number or not 0 <= previous_end <= start <= end <= len(content) or sha(content[start:end]) != row["text_sha256"]:
            raise ValueError("共享页段边界或内容校验失败。")
        page_texts.append(content[start:end].decode("utf-8"))
        previous_end = end
    return page_texts


def download_shared(catalog, identifier, output):
    metadata, content, additional = load_approved(catalog, identifier, include_additional=True)
    validated_pages(metadata, content)
    output = Path(output).expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("下载位置已有内容，请使用新目录。")
    output.mkdir(parents=True, exist_ok=True)
    (output / "content.md").write_bytes(content)
    for name, raw in additional.items():
        (output / name).write_bytes(raw)
    write_json(output / "manifest.json", metadata)
    report = {"status": "shared_downloaded", "origin": "shared", "shared_id": identifier, "source_pdf_available": False, "reviewed_pages": [], "upstream_review_scope": metadata["review_scope"], "quality": metadata["quality"], "scope_description": metadata["scope_description"], "message": "已取得该共享版本的 Markdown，尚未确认与任何本地 PDF 的页码对应；上游核查不是本人核查。"}
    write_json(output / "reuse-report.json", report)
    return report


def reuse_shared(pdf, catalog, identifier, output, *, mode="quick", script="original"):
    metadata, content, additional = load_approved(catalog, identifier, include_additional=True)
    pdf, output = Path(pdf).expanduser().resolve(), Path(output).expanduser().resolve()
    if digest(pdf) != metadata["source_pdf_sha256"] or metadata["script_target"] != script:
        raise ValueError("共享成品的 PDF 或繁简目标不一致，不能按书名直接复用。")
    page_texts = validated_pages(metadata, content)
    machine_texts = validated_pages({**metadata, "pages": metadata["machine_pages"]}, additional["machine.md"]) if additional else page_texts
    if mode not in {"quick", "review"}:
        raise ValueError("不支持的档位。")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("输出位置已有内容，请选择新目录。")
    source = {"schema_version": 1, "mode": mode, "kind": metadata.get("kind", "book"), "method": "shared", "provider": "baidu_async", "script": script, "source_name": pdf.name, "source_sha256": metadata["source_pdf_sha256"], "total_pages": metadata["total_pages"], "origin": "shared", "shared_id": identifier, "shared_quality": metadata["quality"], "upstream_review_scope": metadata["review_scope"], "simulation": False}
    service = engine(output, source)
    created = service.create_job_from_pdf_path(pdf_path=pdf, page_mode=PageMode.PDF, script_target=ScriptTarget(script))
    if created.total_pages != metadata["total_pages"]:
        raise ValueError("共享清单页数与 PDF 不一致。")
    source["job_id"] = created.job_id
    source["source_pdf"] = service.store.input_pdf_path(created.job_id).relative_to(output).as_posix()
    write_json(output / "manifest.json", source)
    for number, text in enumerate(machine_texts, 1):
        service.store.mark_page_done(created.job_id, number, image_path=None, raw_text=text, normalized_text=text, detected_raw=None, detected_norm=None, retry_count=0)
        if mode == "review":
            draft = output / f"review/pages/{number:04d}.md"
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text(page_texts[number - 1], encoding="utf-8")
    if additional:
        shared = output / "shared"
        shared.mkdir()
        (shared / "handwashed.md").write_bytes(additional["handwashed.md"])
        write_json(shared / "provenance.json", {"shared_id": identifier, "handwash": metadata["handwash"], "reviewed_by_current_user": False})
    return export_bundle(output, source, service)
