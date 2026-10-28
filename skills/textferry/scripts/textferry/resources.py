# SPDX-License-Identifier: AGPL-3.0-only
"""Bind locally referenced illustrations to review evidence."""
import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


IMAGE = re.compile(r'!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))|<img\b[^>]*\bsrc\s*=\s*[\"\x27]([^\"\x27]+)', re.I)
REF_IMAGE = re.compile(r'!\[([^\]]*)\](?:\[([^\]]*)\])?')
DEFINITION = re.compile(r'^ {0,3}\[([^\]^]+)\]:\s*(?:<([^>]+)>|(\S+))', re.M)


def image_snapshot(bundle, text, *, base=None):
    root = Path(bundle).resolve()
    base = Path(base).resolve() if base else root
    lines, fence, in_note = [], None, False
    for line in text.splitlines():
        definition = re.match(r'^ {0,3}\[\^[^\]]+\]:', line)
        if definition:
            in_note = True
        elif line.strip() and not line.startswith(('    ', '\t')):
            in_note = False
        if in_note and not definition:
            if line.startswith('    '):
                line = line[4:]
            elif line.startswith('\t'):
                line = line[1:]
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            if fence is None:
                fence = mark[1]
            elif mark[1][0] == fence[0] and len(mark[1]) >= len(fence):
                fence = None
            continue
        if fence is None and not line.startswith(('    ', '\t')):
            lines.append(line)
    text = re.sub(r'(`+)[\s\S]*?\1', '', '\n'.join(lines))
    targets = {next(value for value in match.groups() if value is not None) for match in IMAGE.finditer(text)}
    definitions = {match[1].strip().casefold(): match[2] or match[3] for match in DEFINITION.finditer(text)}
    for match in REF_IMAGE.finditer(text):
        key = (match[2] or match[1]).strip().casefold()
        if key in definitions:
            targets.add(definitions[key])
    result = {}
    for value in sorted(targets):
        parts = urlsplit(value)
        path = (base / unquote(parts.path)).resolve()
        if parts.scheme or parts.netloc or not path.is_relative_to(root):
            result[value] = None
        elif not path.is_file():
            result[path.relative_to(root).as_posix()] = None
        else:
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result
