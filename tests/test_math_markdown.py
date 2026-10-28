import json
import subprocess
import pytest

from textferry.math_markdown import normalize_math_delimiters
from textferry.word_export import pandoc_path, walk


def test_spaced_ocr_math_is_recognized_instead_of_becoming_literal_text():
    source = r'''主变量 $ x_{1}, x_{j_{2}} $，自由变量 $ x_{j_r+1} $。

例 $ \left\{\begin{array}{l}x+y=1,\\x-y=2.\end{array}\right. $
'''
    normalized = normalize_math_delimiters(source)
    assert '$x_{1}, x_{j_{2}}$' in normalized
    assert '$x_{j_r+1}$' in normalized
    try:
        binary=pandoc_path()
    except ValueError:
        pytest.skip('Optional Word/Pandoc extra is not installed')
    parsed = subprocess.run([str(binary), '-f', 'markdown+tex_math_dollars', '-t', 'json'], input=normalized, text=True, capture_output=True, check=True)
    assert len([n for n in walk(json.loads(parsed.stdout)) if n.get('t')=='Math']) == 3


def test_math_normalization_preserves_code_currency_and_tex_content():
    source = r'''代码 `$ x_1 $` 和 ``$ y_2 $``；金额 $ 5 元和 $ 10 元。
转义 \$ 5，变量 $ A $，公式 $ x^2 + y^2 = 1 $。

```tex
$ x_1 $
```

$$
  A = \begin{pmatrix}1 & 2 \\ 3 & 4\end{pmatrix}
$$
'''
    result=normalize_math_delimiters(source)
    assert '`$ x_1 $`' in result and '``$ y_2 $``' in result
    assert '$ 5 元和 $ 10 元' in result
    assert r'\$ 5' in result
    assert '$A$' in result and '$x^2 + y^2 = 1$' in result
    assert '```tex\n$ x_1 $\n```' in result
    assert '$$\nA = ' in result
    assert normalize_math_delimiters(result)==result


def test_simple_math_labels_and_escaped_braces_are_normalized():
    for body in ['(1.7)', 's+1', '|A|', '(i,j)', 'n-1', 'f(x)', r'\{0\}', 'N(A)']:
        assert normalize_math_delimiters('$ '+body+' $')=='$'+body+'$'
    assert normalize_math_delimiters('Costs $ 5 and $ 10.')=='Costs $ 5 and $ 10.'


def test_display_math_has_standalone_delimiters_and_no_blank_internal_paragraphs():
    source='$$ \\begin{aligned}\n\nx&=1\\\\\n\n&=y\n\\end{aligned} $$'
    result=normalize_math_delimiters(source)
    assert result.startswith('$$\n') and result.endswith('\n$$')
    assert '\n\n' not in result


def test_indented_code_and_adjacent_currency_are_not_rewritten():
    for text in ['    $ x_1 $\n', '\t$ y_2 $\n', 'Costs $ 5, $ 10.', '| $ 5 | $ 10 |']:
        assert normalize_math_delimiters(text)==text
