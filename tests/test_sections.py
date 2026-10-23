from app import sections

DRAFT_REPLY = r"""Here is the problem.

## Statement
Let $f(x) = \frac{x^2}{\theta}$. Find $f'(2)$ when $\theta = 4$.

## Track
IB

## Topic
Calculus

## Subtopic
Differentiation

## Difficulty
standard

## Answer type
exact_expression

## Answer
$1$

## Verification
```python
import sympy as sp
x = sp.symbols('x')
result = sp.diff(x**2 / 4, x).subs(x, 2)
```
"""


def test_draft_parses_latex_without_escaping():
    fields, missing = sections.draft(DRAFT_REPLY)
    assert missing == []
    assert fields["statement"] == r"Let $f(x) = \frac{x^2}{\theta}$. Find $f'(2)$ when $\theta = 4$."
    assert fields["track"] == "IB"
    assert fields["answer_type"] == "exact_expression"
    assert fields["answer"] == "1"
    assert fields["verification"].startswith("import sympy as sp")
    assert "```" not in fields["verification"]


def test_unknown_headings_stay_in_their_section():
    reply = "## Solution\nFirst idea.\n\n## Step 2\nSecond idea, so $\\boxed{2}$.\n\n## Hint 1\nA\n## Hint 2\nB\n## Hint 3\nC"
    fields, missing = sections.finish(reply)
    assert missing == []
    assert "## Step 2" in fields["solution"] and "Second idea" in fields["solution"]
    assert fields["hints"] == ["A", "B", "C"]


def test_missing_sections_are_reported():
    _, missing = sections.draft("## Statement\nSomething\n## Answer\n3")
    assert set(missing) == {"track", "topic", "answer_type", "verification"}
    _, missing = sections.finish("## Hint 1\nonly one")
    assert "solution" in missing and "hint 2" in missing


def test_choices_and_distractors_accept_list_styles():
    fields, _ = sections.draft("## Choices\n(A) $1$\n(B) 2\nC. 3\nD) \\frac{1}{2}\n- 5")
    assert fields["choices"] == ["1", "2", "3", "\\frac{1}{2}", "5"]
    fields, _ = sections.finish("## Solution\n$\\boxed{9}$\n## Distractors\n1. 12\n2. $15$\n- 18\n- 20")
    assert fields["distractors"] == ["12", "15", "18", "20"]


def test_heading_variants_and_missing_box():
    fields, missing = sections.finish("## **Solution**\nSo $\\boxed{4}$.\n### Hint 1:\nA\n## Hint 2\nB\n## Hint 3\nC")
    assert missing == [] and fields["boxed"] == "4" and fields["hints"] == ["A", "B", "C"]
    _, missing = sections.finish("## Solution\nNo box.\n## Hint 1\nA\n## Hint 2\nB\n## Hint 3\nC")
    assert any("boxed" in m for m in missing)


def test_boxed_takes_last_and_matches_braces():
    text = r"First $\boxed{1}$, then finally $$\boxed{\frac{3}{8}(m-1)(n-1)}$$."
    assert sections.boxed(text) == r"\frac{3}{8}(m-1)(n-1)"
    assert sections.boxed("no box here") is None
    assert sections.boxed(r"\boxed{\text{(C) } 12}") == r"\text{(C) } 12"
