#!/usr/bin/env python3
"""Install, verify, or update the self-contained Agent Skill."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


AGENT_DIRECTORIES = {
    'generic': '.agents', 'codex': '.agents', 'claude-code': '.claude',
    'cursor': '.cursor', 'gemini-cli': '.gemini',
}


def source_files(source):
    return [p for p in source.rglob('*') if p.is_file() and p.name != '.install.json' and not any(part in {'.venv', '__pycache__', 'build', 'dist', 'node_modules'} or part.endswith('.egg-info') for part in p.relative_to(source).parts)]


def main():
    paths = '; '.join(name + ': ~/' + directory + '/skills/textferry' for name, directory in AGENT_DIRECTORIES.items())
    parser = argparse.ArgumentParser(description='安装可独立运行的 Agent Skill', epilog=paths)
    parser.add_argument('--agent', choices=sorted(AGENT_DIRECTORIES), default='generic', help='选择宿主安装位置，默认使用共享的 .agents 目录')
    parser.add_argument('--target', type=Path, help='自定义 Skill 安装位置，优先于 --agent 默认位置')
    parser.add_argument('--no-setup', action='store_true', help='仅复制文件；不会报告运行环境已准备好')
    parser.add_argument('--update', action='store_true', help='更新现有安装，改写前保存旧文件副本')
    parser.add_argument('--with', dest='extras', choices=['epub', 'word'], action='append', default=[])
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        print('需要 Python 3.10 或以上版本，请让 当前 Agent 先准备运行环境。', file=sys.stderr)
        return 1
    source = Path(__file__).resolve().parent / 'skills/textferry'
    target = (args.target if args.target else Path.home() / AGENT_DIRECTORIES[args.agent] / 'skills/textferry').expanduser().resolve()
    marker = target / '.install.json'
    files = source_files(source)
    # Preflight every managed destination before backups or writes. Local
    # links must never redirect an update into unrelated user files.
    for relative in [Path('.install.json'), *[p.relative_to(source) for p in files]]:
        destination = target / relative
        if any(p.is_symlink() for p in [destination, *list(destination.parents)[:len(relative.parts) - 1]]):
            print('安装位置的受管理文件含符号链接，已停止；请保留链接内容并选择普通目录。', file=sys.stderr)
            return 1
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    backup = None
    if target.exists():
        identity = target / 'SKILL.md'
        if not identity.is_file() or 'name: textferry\n' not in identity.read_text(encoding='utf-8'):
            print('目标目录已有其他内容，请选择新的安装位置。', file=sys.stderr)
            return 1
        differences = [p for p in files if not (target / p.relative_to(source)).is_file() or (target / p.relative_to(source)).read_bytes() != p.read_bytes()]
        if differences and not args.update:
            print('已有安装与此版本不同，请让 当前 Agent 使用 --update 更新；现有文件已保留。', file=sys.stderr)
            return 1
        if differences:
            cache = Path(os.getenv('XDG_CACHE_HOME', str(Path.home() / '.cache')))
            backup = cache / 'textferry/install-backups' / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
            for p in differences:
                old = target / p.relative_to(source)
                if old.is_file():
                    dest = backup / p.relative_to(source)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(old, dest)
    target.mkdir(parents=True, exist_ok=True)
    for path in files:
        dest = target / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or path.read_bytes() != dest.read_bytes():
            shutil.copyfile(path, dest)
    record = {'state': 'copied', 'agent': args.agent, 'files': {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              'extras': sorted(set(previous.get('extras', []) + args.extras))}
    marker.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.no_setup:
        print(json.dumps({'status': 'copied', 'agent': args.agent, 'installed': str(target), 'runtime_prepared': False, 'backup': str(backup) if backup else None,
                          'next': '文件已复制；让 当前 Agent 继续准备环境并执行 doctor 自检。'}, ensure_ascii=False))
        return 0
    try:
        uv = shutil.which('uv')
        if uv:
            command = [uv, 'sync', '--project', str(target), '--no-dev']
            for extra in record['extras']:
                command += ['--extra', extra]
            subprocess.run(command, check=True, stdout=sys.stderr)
        else:
            if not (target / '.venv').exists():
                subprocess.run([sys.executable, '-m', 'venv', str(target / '.venv')], check=True, stdout=sys.stderr)
            runtime = target / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            requirement = str(target) + ('[' + ','.join(record['extras']) + ']' if record['extras'] else '')
            subprocess.run([str(runtime), '-m', 'pip', 'install', requirement], check=True, stdout=sys.stderr)
        runtime = target / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        checked = subprocess.run([str(runtime), str(target / 'scripts/run.py'), 'doctor'], check=True, text=True, capture_output=True)
        result = json.loads(checked.stdout)
        if result.get('status') != 'ready':
            raise ValueError('自检未通过。')
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('运行环境尚未准备完成，文件已保留。请让 当前 Agent 修复环境后重新运行安装；无需先填写密钥。', file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    record['state'] = 'ready'
    marker.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ready', 'agent': args.agent, 'installed': str(target), 'offline_self_test': True, 'backup': str(backup) if backup else None,
                      'next': '安装和离线自检通过。现在提供 PDF 或 EPUB，选择处理方式和格式。'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
