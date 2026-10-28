"""create/run/get/download 子命令的 stdout/stderr、打包入口与显式/兼容下载变体契约测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "textferry"
SCRIPTS_DIR = TOOL_ROOT / "scripts"
RUNTIME_ROOT = TOOL_ROOT / "scripts"


def run_script(script_name: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name), *args],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )


def with_runtime_pythonpath(env: dict[str, str]) -> dict[str, str]:
    patched = env.copy()
    current = patched.get("PYTHONPATH")
    patched["PYTHONPATH"] = str(RUNTIME_ROOT) if not current else f"{RUNTIME_ROOT}{os.pathsep}{current}"
    return patched


def test_module_cli_create_job_uses_packaged_entrypoint(textferry_env: Path, make_pdf) -> None:
    pdf_path = make_pdf(textferry_env / "module-cli.pdf", 1)
    env = with_runtime_pythonpath(os.environ.copy())
    env["TEXTFERRY_DATA_DIR"] = str(textferry_env / "module-cli-data")
    env["TEXTFERRY_MOCK_OCR"] = "1"

    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "textferry.cli",
            "create-job",
            "--pdf-path",
            str(pdf_path),
            "--page-mode",
            "pdf",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    created_payload = json.loads(created.stdout)
    assert created_payload["status"] == "CREATED"
    assert (textferry_env / "module-cli-data" / "jobs" / created_payload["jobId"]).exists()


def test_textferry_module_cli_is_canonical_entrypoint(textferry_env: Path, make_pdf) -> None:
    pdf_path = make_pdf(textferry_env / "material-module-cli.pdf", 1)
    env = with_runtime_pythonpath(os.environ.copy())
    env["TEXTFERRY_DATA_DIR"] = str(textferry_env / "material-module-cli-data")
    env["TEXTFERRY_MOCK_OCR"] = "1"

    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "textferry.cli",
            "create-job",
            "--pdf-path",
            str(pdf_path),
            "--page-mode",
            "pdf",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    created_payload = json.loads(created.stdout)
    assert created_payload["status"] == "CREATED"
    assert (textferry_env / "material-module-cli-data" / "jobs" / created_payload["jobId"]).exists()



def test_cli_flow_outputs_json_and_progress(textferry_env: Path, make_pdf) -> None:
    pdf_path = make_pdf(textferry_env / "cli.pdf", 2)
    env = os.environ.copy()
    env["TEXTFERRY_DATA_DIR"] = str(textferry_env / "cli-data")
    env["TEXTFERRY_MOCK_OCR"] = "1"
    env["TEXTFERRY_OCR_PROVIDER"] = "baidu_async"
    env["TEXTFERRY_ENABLE_READER_DOCX"] = "1"
    env["BAIDU_API_KEY"] = "test-baidu-key"

    created = run_script(
        "create_job.py",
        ["--pdf-path", str(pdf_path), "--page-mode", "pdf"],
        env,
    )
    created_payload = json.loads(created.stdout)
    job_id = created_payload["jobId"]
    assert created_payload["status"] == "CREATED"

    ran = run_script("run_job.py", ["--job-id", job_id], env)
    run_payload = json.loads(ran.stdout)
    assert run_payload["status"] == "FINAL_READY"
    assert "page 1" in ran.stderr

    got = run_script("get_job.py", ["--job-id", job_id], env)
    got_payload = json.loads(got.stdout)
    assert got_payload["jobId"] == job_id
    assert got_payload["status"] == "FINAL_READY"

    output_path = textferry_env / "downloaded.docx"
    downloaded = run_script(
        "download.py",
        ["--job-id", job_id, "--variant", "best", "--output-path", str(output_path)],
        env,
    )
    download_payload = json.loads(downloaded.stdout)
    assert output_path.exists()
    assert download_payload["outputPath"] == str(output_path.resolve())

    machine_output = textferry_env / "downloaded-machine.md"
    downloaded_machine = run_script(
        "download.py",
        ["--job-id", job_id, "--variant", "machine-md", "--output-path", str(machine_output)],
        env,
    )
    machine_payload = json.loads(downloaded_machine.stdout)
    assert machine_output.exists()
    assert machine_payload["variant"] == "machine-md"

    reader_output = textferry_env / "downloaded-reader.docx"
    downloaded_reader = run_script(
        "download.py",
        ["--job-id", job_id, "--variant", "reader-docx", "--output-path", str(reader_output)],
        env,
    )
    reader_payload = json.loads(downloaded_reader.stdout)
    assert reader_output.exists()
    assert reader_payload["variant"] == "reader-docx"

    reader_paged_output = textferry_env / "downloaded-reader-paged.docx"
    downloaded_reader_paged = run_script(
        "download.py",
        ["--job-id", job_id, "--variant", "reader-paged-docx", "--output-path", str(reader_paged_output)],
        env,
    )
    reader_paged_payload = json.loads(downloaded_reader_paged.stdout)
    assert reader_paged_output.exists()
    assert reader_paged_payload["variant"] == "reader-paged-docx"

    reader_flow_output = textferry_env / "downloaded-reader-flow.docx"
    downloaded_reader_flow = run_script(
        "download.py",
        ["--job-id", job_id, "--variant", "reader-flow-docx", "--output-path", str(reader_flow_output)],
        env,
    )
    reader_flow_payload = json.loads(downloaded_reader_flow.stdout)
    assert reader_flow_output.exists()
    assert reader_flow_payload["variant"] == "reader-flow-docx"


def test_download_refuses_to_overwrite_without_force(textferry_env: Path, make_pdf) -> None:
    pdf_path = make_pdf(textferry_env / "overwrite.pdf", 1)
    env = os.environ.copy()
    env["TEXTFERRY_DATA_DIR"] = str(textferry_env / "overwrite-data")
    env["TEXTFERRY_MOCK_OCR"] = "1"
    env["BAIDU_API_KEY"] = "test-baidu-key"

    created = run_script("create_job.py", ["--pdf-path", str(pdf_path), "--page-mode", "pdf"], env)
    job_id = json.loads(created.stdout)["jobId"]
    run_script("run_job.py", ["--job-id", job_id], env)

    output_path = textferry_env / "existing-machine.md"
    output_path.write_text("keep me", encoding="utf-8")
    refused = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "download.py"),
            "--job-id",
            job_id,
            "--variant",
            "machine-md",
            "--output-path",
            str(output_path),
        ],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert refused.returncode != 0
    assert "already exists" in refused.stderr
    assert output_path.read_text(encoding="utf-8") == "keep me"

    forced = run_script(
        "download.py",
        ["--job-id", job_id, "--variant", "machine-md", "--output-path", str(output_path), "--force"],
        env,
    )
    forced_payload = json.loads(forced.stdout)
    assert forced_payload["outputPath"] == str(output_path.resolve())
    assert output_path.read_text(encoding="utf-8") != "keep me"
