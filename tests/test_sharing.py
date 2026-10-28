import json
import shutil
from pathlib import Path

import pymupdf as fitz
import pytest

from textferry.workflow import convert, digest


@pytest.fixture
def finished(tmp_path):
    pdf = tmp_path / "input.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 100), "这是一份自制中文测试材料，检查共享时保留正文及来源。", fontname="china-s")
        doc.save(pdf)
    bundle = tmp_path / "bundle"
    convert(pdf, bundle)
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps({
        "title": "自制材料", "edition": "测试版本一", "scope_description": "自制测试 PDF 共一页，覆盖完整输入；不是实际图书。", "contribution_license": "CC0-1.0", "attribution": "测试贡献者",
        "source_rights": {"basis": "permission", "jurisdiction": "CN", "statement": "测试材料由测试作者创作并授权共享。", "evidence": ["这是测试授权说明，不用于判断真实图书权利。"]},
    }, ensure_ascii=False))
    return pdf, bundle, rights


def prepare(finished, tmp_path):
    from textferry.sharing import prepare_share
    pdf, bundle, rights = finished
    candidate = tmp_path / "candidate"
    result = prepare_share(bundle, rights, candidate)
    return candidate, result


def catalog_for(candidate, tmp_path, *, status="approved"):
    manifest = json.loads((candidate / "manifest.json").read_text())
    library = tmp_path / "library"
    target = library / "artifacts" / manifest["id"]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, target)
    entry = {key: manifest[key] for key in ["id", "title", "edition", "source_pdf_sha256", "total_pages", "script_target", "quality"]}
    entry.update({"status": status, "manifest": f"artifacts/{manifest['id']}/manifest.json", "manifest_sha256": digest(target / "manifest.json")})
    index = library / "index.json"
    index.write_text(json.dumps({"schema_version": 1, "entries": [entry]}, ensure_ascii=False))
    return index, manifest, target


def test_share_exports_only_public_text_and_explicit_metadata(finished, tmp_path):
    candidate, result = prepare(finished, tmp_path)
    assert sorted(p.name for p in candidate.iterdir()) == ["README.md", "content.md", "manifest.json"]
    text = "\n".join(p.read_text() for p in candidate.iterdir())
    assert str(tmp_path) not in text
    assert "input.pdf" not in text
    manifest = json.loads((candidate / "manifest.json").read_text())
    assert manifest["source_pdf_sha256"] == digest(finished[0])
    assert manifest["quality"] == "machine"
    assert manifest["rights_review"] == "contributor_statement_unverified"
    assert result["publication_status"] == "local_draft"


def test_unknown_rights_cannot_prepare_public_candidate(finished, tmp_path):
    from textferry.sharing import prepare_share
    rights = json.loads(finished[2].read_text())
    rights["source_rights"]["basis"] = "unknown"
    finished[2].write_text(json.dumps(rights))
    with pytest.raises(ValueError, match="权利"):
        prepare_share(finished[1], finished[2], tmp_path / "candidate")


def test_prepare_refuses_private_paths_in_document(finished, tmp_path):
    from textferry.sharing import prepare_share
    page = next((finished[1] / "_work/jobs").glob("*/pages/page_0001/normalized.txt"))
    page.write_text("私人路径 /Users/private-person/Documents/research.md")
    with pytest.raises(ValueError, match="本机路径"):
        prepare_share(finished[1], finished[2], tmp_path / "candidate")


def test_find_shared_matches_exact_pdf_and_separates_title_candidates(finished, tmp_path):
    from textferry.sharing import find_shared
    candidate, _ = prepare(finished, tmp_path)
    index, manifest, _ = catalog_for(candidate, tmp_path)
    result = find_shared(finished[0], index)
    assert [item["id"] for item in result["exact_matches"]] == [manifest["id"]]
    different = tmp_path / "different.pdf"
    with fitz.open(finished[0]) as doc:
        doc.set_metadata({"title": "different scan container"})
        doc.save(different)
    result = find_shared(different, index, title="自制材料")
    assert result["exact_matches"] == []
    assert len(result["title_candidates"]) == 1


def test_reuse_verified_result_without_calling_ocr(finished, tmp_path, monkeypatch):
    from textferry.sharing import reuse_shared
    from textferry.service import TextFerryService
    candidate, _ = prepare(finished, tmp_path)
    index, manifest, _ = catalog_for(candidate, tmp_path)
    monkeypatch.setattr(TextFerryService, "run_job", lambda *args, **kwargs: pytest.fail("复用不应调用 OCR"))
    output = tmp_path / "reused"
    result = reuse_shared(finished[0], index, manifest["id"], output, mode="review")
    assert result["origin"] == "shared"
    assert result["reviewed_pages"] == []
    assert result["status"] == "awaiting_review"
    assert (output / "review/pages/0001.png").exists()
    assert "共享" in (output / "machine.md").read_text()


def test_reuse_rejects_tampered_text_and_unapproved_entries(finished, tmp_path):
    from textferry.sharing import reuse_shared
    candidate, _ = prepare(finished, tmp_path)
    index, manifest, target = catalog_for(candidate, tmp_path)
    (target / "content.md").write_text("篡改内容")
    with pytest.raises(ValueError, match="校验"):
        reuse_shared(finished[0], index, manifest["id"], tmp_path / "reused")
    data = json.loads(index.read_text())
    data["entries"][0]["status"] = "pending"
    index.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="审核"):
        reuse_shared(finished[0], index, manifest["id"], tmp_path / "reused")


def test_consent_applies_to_one_candidate_version_and_can_decline(finished, tmp_path):
    from textferry.sharing import record_share_decision, share_status
    candidate, _ = prepare(finished, tmp_path)
    repository = "example-owner/textferry"
    status = share_status(finished[1], candidate, repository=repository)
    assert status["should_ask"]
    record_share_decision(finished[1], candidate, "no")
    assert not share_status(finished[1], candidate, repository=repository)["should_ask"]
    assert not share_status(finished[1], candidate, repository=repository)["consented"]
    record_share_decision(finished[1], candidate, "yes", repository=repository)
    assert share_status(finished[1], candidate, repository=repository)["consented"]
    assert not share_status(finished[1], candidate, repository="another-owner/another-repo")["consented"]
    content = candidate / "content.md"
    content.write_text(content.read_text() + "\n后续修订。")
    assert not share_status(finished[1], candidate)["consented"]


def test_catalog_paths_cannot_escape_library(finished, tmp_path):
    from textferry.sharing import reuse_shared
    candidate, _ = prepare(finished, tmp_path)
    index, manifest, _ = catalog_for(candidate, tmp_path)
    data = json.loads(index.read_text())
    data["entries"][0]["manifest"] = "../private/manifest.json"
    index.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="路径"):
        reuse_shared(finished[0], index, manifest["id"], tmp_path / "reused")


def test_share_scope_must_be_explicit(finished, tmp_path):
    from textferry.sharing import prepare_share
    data = json.loads(finished[2].read_text())
    data.pop("scope_description")
    finished[2].write_text(json.dumps(data))
    with pytest.raises(ValueError, match="范围"):
        prepare_share(finished[1], finished[2], tmp_path / "candidate")


def test_consent_cannot_be_attached_to_another_source(finished, tmp_path):
    from textferry.sharing import share_status
    candidate, _ = prepare(finished, tmp_path)
    metadata = json.loads((candidate / "manifest.json").read_text())
    metadata["source_pdf_sha256"] = "0" * 64
    (candidate / "manifest.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="来源"):
        share_status(finished[1], candidate)


def test_share_privacy_check_covers_both_compatible_env_names(finished, tmp_path, monkeypatch):
    from textferry.sharing import prepare_share
    monkeypatch.setenv("PADDLEOCR_ACCESS_TOKEN", "synthetic-primary-access-token")
    monkeypatch.setenv("BAIDU_API_KEY", "synthetic-secondary-compat-key")
    page = next((finished[1] / "_work/jobs").glob("*/pages/page_0001/normalized.txt"))
    page.write_text("正文中误带 synthetic-secondary-compat-key")
    with pytest.raises(ValueError, match="凭证"):
        prepare_share(finished[1], finished[2], tmp_path / "candidate")


def test_cli_can_search_local_catalog(finished, tmp_path, capsys):
    from textferry.workflow import main
    candidate, _ = prepare(finished, tmp_path)
    index, metadata, _ = catalog_for(candidate, tmp_path)
    assert main(["find-shared", str(finished[0]), "--catalog", str(index)]) == 0
    assert metadata["id"] in json.loads(capsys.readouterr().out)["exact_matches"][0]["id"]


def test_edited_rights_cannot_reuse_a_prepared_consent(finished, tmp_path):
    from textferry.sharing import record_share_decision
    candidate, _ = prepare(finished, tmp_path)
    data = json.loads((candidate / "manifest.json").read_text())
    data["source_rights"]["basis"] = "unknown"
    (candidate / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="权利"):
        record_share_decision(finished[1], candidate, "yes", repository="example-owner/textferry")


def test_private_extra_rights_fields_are_not_exported(finished, tmp_path):
    data = json.loads(finished[2].read_text())
    data["source_rights"]["private_contract_notes"] = "not meant for public output"
    finished[2].write_text(json.dumps(data))
    candidate, _ = prepare(finished, tmp_path)
    assert "private_contract_notes" not in (candidate / "manifest.json").read_text()


def test_public_consent_requires_a_concrete_github_destination(finished, tmp_path):
    from textferry.sharing import record_share_decision
    candidate, _ = prepare(finished, tmp_path)
    with pytest.raises(ValueError, match="仓库"):
        record_share_decision(finished[1], candidate, "yes")


def test_title_only_lookup_does_not_require_having_a_pdf(finished, tmp_path):
    from textferry.sharing import find_shared
    candidate, _ = prepare(finished, tmp_path)
    index, metadata, _ = catalog_for(candidate, tmp_path)
    result = find_shared(None, index, title="自制材料")
    assert result["exact_matches"] == []
    assert result["title_candidates"][0]["id"] == metadata["id"]


def test_download_approved_markdown_without_pdf(finished, tmp_path):
    from textferry.sharing import download_shared
    candidate, _ = prepare(finished, tmp_path)
    index, metadata, _ = catalog_for(candidate, tmp_path)
    output = tmp_path / "downloaded"
    result = download_shared(index, metadata["id"], output)
    assert result["status"] == "shared_downloaded"
    assert result["source_pdf_available"] is False
    assert result["reviewed_pages"] == []
    assert digest(output / "content.md") == metadata["content_sha256"]
    assert result["upstream_review_scope"] == metadata["review_scope"]


def test_share_refuses_image_links_instead_of_publishing_broken_images(finished, tmp_path):
    from textferry.sharing import prepare_share
    page = next((finished[1] / '_work/jobs').glob('*/pages/page_0001/normalized.txt'))
    (finished[1] / 'assets').mkdir()
    (finished[1] / 'assets/diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    page.write_text('Illustration: ![diagram](assets/diagram.svg)')
    with pytest.raises(ValueError, match='图片'):
        prepare_share(finished[1], finished[2], tmp_path / 'candidate')
    assert not (tmp_path / 'candidate/content.md').exists()


def test_old_approved_record_with_images_is_not_downloaded_as_complete(finished, tmp_path):
    from textferry.sharing import load_approved
    candidate, _ = prepare(finished, tmp_path)
    index, manifest, target = catalog_for(candidate, tmp_path)
    content = target / 'content.md'
    content.write_text(content.read_text() + '\n![missing](assets/missing.png)\n')
    manifest['content_sha256'] = digest(content)
    (target / 'manifest.json').write_text(json.dumps(manifest))
    catalog = json.loads(index.read_text())
    catalog['entries'][0]['manifest_sha256'] = digest(target / 'manifest.json')
    index.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match='图片'):
        load_approved(index, manifest['id'])
