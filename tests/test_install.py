import subprocess
import sys
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_installed_skill_runs_without_source_checkout(tmp_path):
    target = tmp_path / "installed" / "textferry"
    result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--target", str(target), "--no-setup"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (target / "LICENSE").exists()
    assert not (target / ".venv").exists()
    result = subprocess.run([sys.executable, str(target / "scripts/run.py"), "--help"], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "convert" in result.stdout
    (target / "mine.md").write_text("keep")
    result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--target", str(target), "--no-setup"], text=True, capture_output=True)
    assert result.returncode == 0
    assert (target / "mine.md").read_text() == "keep"


def test_copy_only_install_is_explicit_without_uv(tmp_path):
    import json
    target = tmp_path / "textferry"
    env = os.environ.copy()
    env["PATH"] = ""
    env["TEXTFERRY_CONFIG_DIR"] = str(tmp_path / "config")
    result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--target", str(target), "--no-setup"], text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["status"] == "copied" and status["runtime_prepared"] is False
    assert not (tmp_path / "config/credentials.json").exists()


def test_installed_skill_documentation_resolves_within_skill(tmp_path):
    import re
    from urllib.parse import unquote, urlsplit

    target = tmp_path / "textferry"
    result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--target", str(target), "--no-setup"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    documents = [target / "SKILL.md", *list((target / "references").rglob("*.md"))]
    for path in documents:
        text = path.read_text(encoding="utf-8")
        assert "[[" not in text, f"unresolved workspace links in {path.name}"
        for link in re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", text):
            parts = urlsplit(link)
            if parts.scheme or not parts.path:
                continue
            reference = (path.parent / unquote(parts.path)).resolve()
            assert reference.is_relative_to(target), (path.name, link)
            assert reference.exists(), (path.name, link)


def test_installed_workflow_uses_only_its_files_in_an_empty_directory(tmp_path):
    import json

    target = tmp_path / 'installed skill'
    result = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', str(target), '--no-setup'], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    unrelated = tmp_path / 'empty working directory'
    unrelated.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith(('TEXTFERRY_', 'PYTHONPATH', 'BAIDU_', 'PADDLEOCR_', 'SILICONFLOW_', 'XDG_'))}
    env.update({'HOME': str(tmp_path / 'home'), 'XDG_CONFIG_HOME': str(tmp_path / 'config'), 'XDG_CACHE_HOME': str(tmp_path / 'cache'), 'PYTHONNOUSERSITE': '1'})
    code = r'''
import json, sys
from pathlib import Path
skill, forbidden = map(Path, sys.argv[1:])
def audit(event, args):
    if event == 'socket.connect':
        raise AssertionError('text conversion must work without network')
    if event == 'open' and isinstance(args[0], str):
        assert not Path(args[0]).resolve().is_relative_to(forbidden), args[0]
sys.addaudithook(audit)
sys.path.insert(0, str(skill / 'scripts'))
import pymupdf as fitz
from textferry import workflow, handwash
from textferry.config import get_textferry_settings
assert Path(workflow.__file__).is_relative_to(skill)
assert get_textferry_settings().models_toml_path.is_relative_to(skill)
assert workflow.onboarding(prepare=True)['status'] == 'needs_setup'
source = Path('input.pdf')
with fitz.open() as doc:
    doc.new_page().insert_text((72, 72), 'Source paragraph.')
    doc.save(source)
result = workflow.convert(source, Path('bundle'), mode='review')
assert result['status'] == 'awaiting_review'
handwash.prepare_handwash('bundle')
workflow.review_page('bundle', 1, checks='body,order,notes,structure', reviewer='Test fixture', note='Synthetic single-page text and boundaries.', issues=[])
result = handwash.export_handwash('bundle', reviewer='Test fixture', note='Synthetic one-page fixture; no cross-page boundaries.')
assert result['current']
assert 'Source paragraph.' in Path('bundle/handwashed.md').read_text()
assert Path('bundle/machine.md').exists()
print(json.dumps({'status':'passed','source_checked':False,'network_calls':0}))
'''
    result = subprocess.run([sys.executable, '-c', code, str(target), str(ROOT / 'skills/textferry/scripts')], cwd=unrelated, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'passed'


def test_no_uv_bootstrap_failure_is_not_reported_as_ready(tmp_path):
    target = tmp_path / 'installed'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TEXTFERRY_', 'PADDLEOCR_', 'BAIDU_', 'SILICONFLOW_'))}
    env.update(PATH='', PIP_NO_INDEX='1', TEXTFERRY_CONFIG_DIR=str(tmp_path / 'config'))
    result = subprocess.run([sys.executable, '-S', str(ROOT / 'install.py'), '--target', str(target)], text=True, capture_output=True, env=env)
    assert result.returncode != 0
    assert '环境' in result.stdout + result.stderr
    assert not (tmp_path / 'config/credentials.json').exists()


def test_update_preserves_local_files_and_backs_up_changed_instructions(tmp_path):
    import json
    target = tmp_path / 'agents/skills/installed'
    env = os.environ.copy(); env['XDG_CACHE_HOME'] = str(tmp_path / 'cache')
    first = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', str(target), '--no-setup'], text=True, capture_output=True, env=env)
    assert first.returncode == 0
    (target / 'mine.md').write_text('Keep my local file.')
    skill = target / 'SKILL.md'; skill.write_text(skill.read_text() + '\nLocal instruction.\n')
    result = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', str(target), '--no-setup', '--update'], text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    backup = Path(json.loads(result.stdout)['backup'])
    assert 'Local instruction.' in (backup / 'SKILL.md').read_text()
    assert (target / 'mine.md').read_text() == 'Keep my local file.'
    assert not backup.is_relative_to(target.parent)


def test_doctor_verifies_without_requesting_ocr_key(tmp_path):
    import json
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TEXTFERRY_', 'PADDLEOCR_', 'BAIDU_', 'SILICONFLOW_'))}
    env['TEXTFERRY_CONFIG_DIR'] = str(tmp_path / 'config')
    result = subprocess.run([sys.executable, str(ROOT / 'skills/textferry/scripts/run.py'), 'doctor'], text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['offline_self_test']
    assert not (tmp_path / 'config').exists()


@pytest.mark.parametrize('agent,directory', [
    ('generic', '.agents'), ('codex', '.agents'), ('claude-code', '.claude'),
    ('cursor', '.cursor'), ('gemini-cli', '.gemini'),
])
def test_installer_selects_host_directory_without_changing_other_hosts(tmp_path, monkeypatch, capsys, agent, directory):
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location('portable_installer', ROOT / 'install.py')
    installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, 'argv', ['install.py', '--agent', agent, '--no-setup'])
    assert installer.main() == 0
    status = json.loads(capsys.readouterr().out)
    expected = tmp_path / directory / 'skills/textferry'
    assert Path(status['installed']) == expected
    assert status['agent'] == agent
    assert (expected / 'SKILL.md').is_file()
    assert all(not (tmp_path / other).exists() for other in {'.agents', '.claude', '.cursor', '.gemini'} - {directory})


def test_explicit_target_overrides_host_default(tmp_path):
    import json
    target = tmp_path / 'custom skills/textferry'
    result = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--agent', 'claude-code', '--target', str(target), '--no-setup'], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)['installed']) == target


def test_core_runs_without_optional_openai_metadata(tmp_path):
    import json
    target = tmp_path / 'generic-skill'
    result = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', str(target), '--no-setup'], text=True, capture_output=True)
    assert result.returncode == 0
    (target / 'agents/openai.yaml').unlink()
    result = subprocess.run([sys.executable, str(target / 'scripts/run.py'), 'doctor'], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'ready'


@pytest.mark.parametrize('link_kind', ['file', 'directory', 'marker'])
def test_update_refuses_managed_symlinks_before_touching_external_files(tmp_path, link_kind):
    import json
    import shutil
    target = tmp_path / 'installed'
    command = [sys.executable, str(ROOT / 'install.py'), '--target', str(target), '--no-setup']
    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    outside = tmp_path / 'outside'
    outside.mkdir()
    if link_kind == 'directory':
        victim = outside / 'run.py'
        victim.write_text('Keep this external file.')
        shutil.rmtree(target / 'scripts')
        (target / 'scripts').symlink_to(outside, target_is_directory=True)
    else:
        relative = 'scripts/run.py' if link_kind == 'file' else '.install.json'
        victim = outside / 'keep.txt'
        victim.write_text('Keep this external file.' if link_kind == 'file' else json.dumps({'files': {}}))
        (target / relative).unlink()
        (target / relative).symlink_to(victim)
    before = victim.read_bytes()
    result = subprocess.run(command + ['--update'], text=True, capture_output=True)
    assert result.returncode != 0
    assert victim.read_bytes() == before
    assert '符号链接' in result.stderr
