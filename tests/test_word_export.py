# SPDX-License-Identifier: AGPL-3.0-only
"""Word regressions; install the optional word extra to run these tests."""
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import os
import subprocess

import pytest

from textferry.word_export import pandoc_path, write_word

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'

SAMPLE = r'''# 数学排版测试

中文正文和脚注。[^note]

## 行列式与矩阵

行内公式 $x^2+y^2=1$。

$$
A=\begin{pmatrix}1 & 2 \\ 3 & 4\end{pmatrix},\quad\det A=-2.
$$

| 项目 | 数值 |
|---|---|
| 行列式 | -2 |

[^note]: 自制脚注。
'''


@pytest.fixture
def word_document(tmp_path):
    try:
        pandoc_path()
    except ValueError:
        pytest.skip('Install the optional word dependencies for Word export tests')
    output = tmp_path / 'sample.docx'
    write_word(SAMPLE, output, tmp_path)
    return output


def test_referenced_word_styles_are_defined(word_document):
    # A dangling Compact style can detach table text in LibreOffice. Checking
    # the complete reference graph also covers first paragraphs and footnotes.
    with ZipFile(word_document) as archive:
        styles = ET.fromstring(archive.read('word/styles.xml'))
        defined = {node.get(W + 'styleId') for node in styles.findall(W + 'style')}
        references = set()
        for name in archive.namelist():
            if name.startswith('word/') and name.endswith('.xml'):
                root = ET.fromstring(archive.read(name))
                for tag in ('pStyle', 'rStyle', 'tblStyle', 'basedOn', 'next', 'link'):
                    references.update(node.get(W + 'val') for node in root.iter(W + tag))
        assert references <= defined, f'Undefined Word styles: {sorted(references - defined)}'


def test_word_keeps_native_equations_footnotes_and_table_cells(word_document):
    with ZipFile(word_document) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        assert len(list(root.iter(M + 'oMath'))) == 2
        matrix = root.find('.//' + M + 'm')
        assert matrix is not None
        assert len(matrix.findall(M + 'mr')) == 2
        assert [''.join(row.itertext()) for row in matrix.findall(M + 'mr')] == ['12', '34']
        table = root.find('.//' + W + 'tbl')
        rows = table.findall(W + 'tr')
        assert [[''.join(cell.itertext()) for cell in row.findall(W + 'tc')] for row in rows] == [
            ['项目', '数值'], ['行列式', '-2'],
        ]
        footnotes = ET.fromstring(archive.read('word/footnotes.xml'))
        assert '自制脚注。' in ''.join(footnotes.itertext())
        assert root.find('.//' + W + 'footnoteReference') is not None


def test_extended_equals_and_spaced_math_remain_native(tmp_path):
    try:
        pandoc_path()
    except ValueError:
        pytest.skip('Install optional Word dependencies')
    output=tmp_path/'equations.docx'
    write_word(r'''行内 $ x_1 $。

$$D\xlongequal[r_3-4r_1]{\substack{r_1-r_2\\r_2-2r_1}}D'$$
''',output,tmp_path)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        assert len(list(root.iter(M+'oMath')))==2
        text=''.join(root.itertext())
        assert 'r' in text and '=' in text and '$' not in text


def test_default_tall_image_leaves_room_for_surrounding_text(tmp_path):
    try:
        pandoc_path()
    except ValueError:
        pytest.skip('Install optional Word dependencies')
    import pymupdf as fitz
    image=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,1200,1800),False)
    image.clear_with(255)
    image.save(tmp_path/'tall.png')
    output=tmp_path/'image.docx'
    write_word('# 标题\n\n图前文字。\n\n![测试](tall.png)',output,tmp_path)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        extent=root.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent')
        assert int(extent.get('cy')) <= 18*360000


def test_matrix_trailing_rows_and_alignment_marks_are_portable(tmp_path):
    try:
        pandoc_path()
    except ValueError:
        pytest.skip('Install optional Word dependencies')
    output=tmp_path/'matrix.docx'
    write_word(r'''$$
\begin{aligned}
D&=\begin{vmatrix}a&b\\c&d\\\end{vmatrix}\\
&=\begin{vmatrix}a&b\\c&\\\end{vmatrix}
\end{aligned}
$$''',output,tmp_path)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        shapes=[]
        for matrix in root.iter(M+'m'):
            rows=matrix.findall(M+'mr')
            assert len(rows)==2
            widths=[len(row.findall(M+'e')) for row in rows]
            assert len(set(widths))==1
            shapes.append((len(rows),widths[0]))
            assert all(len(cell)>0 for row in rows for cell in row.findall(M+'e'))
        assert shapes.count((2,2))==2
        assert '&' not in ''.join(root.itertext())


def test_compatible_math_embeds_cached_images_and_keeps_simple_inline_native(tmp_path, monkeypatch):
    node = os.environ.get('TEXTFERRY_NODE')
    if not node:
        pytest.skip('Set TEXTFERRY_NODE and renderer module paths for the optional math integration test')
    probe = subprocess.run([node, '-e', 'require("mathjax-full/js/mathjax.js");require("sharp")'], capture_output=True)
    if probe.returncode:
        pytest.skip('Install mathjax-full and sharp for the optional math integration test')
    monkeypatch.setenv('TEXTFERRY_WORD_EQUATIONS', 'compatible')
    text = r'''简单行内 $x^2+y^2=1$，复杂行内 $\begin{smallmatrix}1&2\\3&4\end{smallmatrix}$。

$$A=\begin{pmatrix}1&2&3&4\\5&6&7&8\\9&10&11&12\\13&14&15&16\end{pmatrix}$$

$$D\xlongequal[r_3-4r_1]{\substack{r_1-r_2\\r_2-2r_1}}D'$$
'''
    output = tmp_path / 'compatible.docx'
    result = write_word(text, output, tmp_path)
    with ZipFile(output) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        assert len(list(root.iter(M + 'oMath'))) == 1
        assert len(root.findall('.//' + W + 'drawing')) == 3
        assert len([name for name in archive.namelist() if name.startswith('word/media/')]) == 3
        descriptions = [node.get('descr', '') for node in root.iter('{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr')]
        assert any('xlongequal' in text for text in descriptions)
    assert result['native_equations'] == 1
    assert result['image_equations'] == 3
    cached = list((tmp_path / 'word-math').glob('*.png'))
    assert len(cached) == 3
    timestamps = {path.name: path.stat().st_mtime_ns for path in cached}
    write_word(text, tmp_path / 'again.docx', tmp_path)
    assert timestamps == {path.name: path.stat().st_mtime_ns for path in cached}


def test_compatible_fraction_keeps_its_visible_rule(tmp_path, monkeypatch):
    if not os.environ.get('TEXTFERRY_NODE'):
        pytest.skip('Set TEXTFERRY_NODE and module paths for renderer integration tests')
    monkeypatch.setenv('TEXTFERRY_WORD_EQUATIONS', 'compatible')
    write_word(r'$$\frac{111111}{888888}$$', tmp_path / 'fraction.docx', tmp_path)
    import pymupdf as fitz
    image = fitz.Pixmap(str(next((tmp_path / 'word-math').glob('*.png'))))
    pixels = image.samples
    longest = 0
    for y in range(image.height):
        run = 0
        for x in range(image.width):
            offset = y * image.stride + x * image.n
            dark = max(pixels[offset:offset + 3]) < 100 and (not image.alpha or pixels[offset + 3] > 200)
            run = run + 1 if dark else 0
            longest = max(longest, run)
    assert longest > image.width * .75, 'The long fraction rule disappeared from the rendered PNG'


def test_compatible_tagged_cases_preserves_equation_number(tmp_path, monkeypatch):
    if not os.environ.get('TEXTFERRY_NODE'):
        pytest.skip('Set TEXTFERRY_NODE and module paths for renderer integration tests')
    monkeypatch.setenv('TEXTFERRY_WORD_EQUATIONS', 'compatible')
    tex = r'\begin{cases}3x+2y+z=39,\\2x+3y+z=34,\\x+2y+3z=26,\end{cases}\tag{1.1}'
    result = write_word('$$' + tex + '$$', tmp_path / 'tag.docx', tmp_path)
    assert result['image_equations'] == 1
    import json
    metadata = json.loads(next((tmp_path / 'word-math').glob('*.json')).read_text())
    assert metadata['tex'].strip() == tex
    assert metadata['render_tex'].strip().endswith(r'\qquad\text{(1.1)}')


def test_standalone_exercise_labels_are_visible_in_word(tmp_path):
    try:
        pandoc_path()
    except ValueError:
        pytest.skip('Install optional Word dependencies')
    output=tmp_path/'labels.docx'
    write_word('(1)\n\n$$x=1$$\n\n(2)\n\n$$y=2$$',output,tmp_path)
    with ZipFile(output) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        visible=''.join(root.itertext())
        assert '(1)' in visible and '(2)' in visible


def test_bare_summation_operator_uses_compatible_image(tmp_path,monkeypatch):
    if not os.environ.get('TEXTFERRY_NODE'):
        pytest.skip('Set optional renderer environment')
    monkeypatch.setenv('TEXTFERRY_WORD_EQUATIONS','compatible')
    result=write_word(r'式中 $\sum_{i=1}^{n}$ 表示求和。',tmp_path/'sum.docx',tmp_path)
    assert result['image_equations']==1
    assert result['native_equations']==0


def test_equation_number_stays_with_preceding_display(tmp_path):
    try:
        pandoc_path()
    except ValueError:
        pytest.skip('Install optional Word dependencies')
    output=tmp_path/'number.docx'
    write_word('$$x=1$$\n\n(7.8)',output,tmp_path)
    from docx import Document
    paragraphs=Document(output).paragraphs
    i=next(i for i,p in enumerate(paragraphs) if p.text.strip()=='(7.8)')
    assert paragraphs[i-1].paragraph_format.keep_with_next is True


def test_source_list_numbers_are_literal_and_code_is_unchanged(tmp_path):
    try:
        pandoc_path()
    except ValueError:
        pytest.skip('Install optional Word dependencies')
    output=tmp_path/'source-labels.docx'
    write_word('(3) 第三组。\n\n(7) 第七组。\n\n```\n(1)\n```',output,tmp_path)
    with ZipFile(output) as archive:
        visible=''.join(ET.fromstring(archive.read('word/document.xml')).itertext())
        assert '(3) 第三组。' in visible and '(7) 第七组。' in visible
        assert '(1)' in visible and '(1\\)' not in visible


def test_compatible_render_failure_preserves_existing_output(tmp_path, monkeypatch):
    if not os.environ.get('TEXTFERRY_NODE'):
        pytest.skip('Set TEXTFERRY_NODE and module paths for renderer integration tests')
    monkeypatch.setenv('TEXTFERRY_WORD_EQUATIONS', 'compatible')
    output = tmp_path / 'existing.docx'
    output.write_bytes(b'existing document must survive')
    with pytest.raises(ValueError, match='兼容公式渲染失败'):
        write_word(r'$$\thisCommandDoesNotExistForWordMath{a}$$', output, tmp_path)
    assert output.read_bytes() == b'existing document must survive'
