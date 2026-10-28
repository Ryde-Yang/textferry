# SPDX-License-Identifier: AGPL-3.0-only
"""Conservative normalization of OCR's spaced inline math delimiters."""
import re

_INLINE = re.compile(r'(?<![\\$])\$(?!\$)((?:\\[^\n]|[^\\$\n])+?)\$(?!\$)')
_CODE = re.compile(r'(?P<ticks>`+)(?!`)[\s\S]*?(?P=ticks)(?!`)')
_DISPLAY = re.compile(r'(?<![\\$])\$\$(?!\$)([\s\S]*?)(?<![\\$])\$\$(?!\$)')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
_MATH = re.compile(r'\\(?:[A-Za-z]+|[{}])|[_^=<>∈∉≤≥≠±×÷]')
_ATOMS = re.compile(r'[A-Za-z0-9\u0370-\u03ff]+(?:\s*[,，;:]\s*[A-Za-z0-9\u0370-\u03ff]+)*')
_SIMPLE = re.compile(r'[A-Za-z0-9\u0370-\u03ff\s()+*/|.,:;\[\]{}-]+')


def _normalize_prose(text):
    def replace(match):
        body = match[1]
        stripped = body.strip()
        if re.match(r'[ \t]*\d', match.string[match.end():]) and not _MATH.search(stripped):
            return match[0]
        simple = _SIMPLE.fullmatch(stripped) and not re.search(r'[A-Za-z]{2,}', stripped)
        if stripped and body != stripped and (_MATH.search(stripped) or _ATOMS.fullmatch(stripped) or simple):
            return '$' + stripped + '$'
        return match[0]

    def prose(value):
        value = _DISPLAY.sub(lambda m: '$$\n' + '\n'.join(line for line in m[1].strip().splitlines() if line.strip()) + '\n$$', value)
        return _INLINE.sub(replace, value)

    result, start = [], 0
    for code in _CODE.finditer(text):
        result.extend([prose(text[start:code.start()]), code[0]])
        start = code.end()
    result.append(prose(text[start:]))
    return ''.join(result)


def normalize_math_delimiters(text):
    result, prose = [], []
    fence, display = None, False
    for line in text.splitlines(keepends=True):
        mark = _FENCE.match(line)
        if fence is not None:
            result.append(line)
            if mark and mark[1][0] == fence[0] and len(mark[1]) >= len(fence) and not line[mark.end():].strip():
                fence = None
        elif mark and not display:
            result.append(_normalize_prose(''.join(prose)))
            prose = []
            fence = mark[1]
            result.append(line)
        elif not display and line.startswith(('    ', '\t')):
            result.append(_normalize_prose(''.join(prose)))
            prose = []
            result.append(line)
        else:
            prose.append(line)
            if len(re.findall(r'(?<![\\$])\$\$(?!\$)', line)) % 2:
                display = not display
    result.append(_normalize_prose(''.join(prose)))
    return ''.join(result)
