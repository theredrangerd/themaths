import json

from app.jsonfix import repair


def load(raw):
    return json.loads(repair(raw))


def test_unescaped_latex_survives():
    raw = r'{"s": "$1 \times \frac{4}{5} \neq \beta \right) \text{x} \sqrt{2} \cdot \underline{a}$"}'
    assert load(raw)["s"] == r"$1 \times \frac{4}{5} \neq \beta \right) \text{x} \sqrt{2} \cdot \underline{a}$"


def test_correctly_escaped_latex_unchanged():
    raw = json.dumps({"s": r"$\frac{1}{2} \times \theta$"})
    assert load(raw)["s"] == r"$\frac{1}{2} \times \theta$"


def test_real_escapes_kept():
    raw = r'{"s": "line one\nNext line\tTabbed \"quoted\" é"}'
    assert load(raw)["s"] == 'line one\nNext line\tTabbed "quoted" é'


def test_newline_before_ordinary_word_kept():
    assert load(r'{"s": "a\nbecause"}')["s"] == "a\nbecause"
    assert load(r'{"s": "a\nThen b"}')["s"] == "a\nThen b"
