import pytest

from app.verify import agrees, check


def p(answer, kind="exact_expression", choices=None):
    return {"answer": answer, "answer_type": kind, "choices": choices or []}


@pytest.mark.parametrize(
    "user,truth",
    [
        ("3/4", "\\frac{3}{4}"),
        ("\\dfrac{3}{4}", "0.75"),
        ("2sqrt(3)", "\\sqrt{12}"),
        ("$2\\sqrt{3}$", "2*sqrt(3)"),
        ("x = 5", "5"),
        ("(x+1)^2", "x^2 + 2x + 1"),
        ("\\frac{\\pi}{6}", "pi/6"),
        ("e^2", "\\exp(2)"),
        ("ln 8", "3 ln 2"),
        ("\\boxed{12}", "12"),
        ("1 + i", "1 + \\sqrt{-1}"),
    ],
)
def test_equivalent_forms_are_correct(user, truth):
    assert check(user, p(truth)).result == "correct"


@pytest.mark.parametrize("user,truth", [("3/5", "3/4"), ("x^2+1", "(x+1)^2"), ("-12", "12")])
def test_wrong_answers_are_incorrect(user, truth):
    assert check(user, p(truth)).result == "incorrect"


def test_decimal_rounding_accepted_at_three_sig_figs():
    v = check("1.41", p("sqrt(2)"))
    assert v.result == "correct" and "3 significant" in v.note
    assert check("1.4", p("sqrt(2)")).result == "incorrect"
    assert check("1.42", p("sqrt(2)")).result == "incorrect"


def test_integer():
    assert check("42", p("42", "integer")).result == "correct"
    assert check("41", p("42", "integer")).result == "incorrect"


def test_multiple_choice_by_letter_and_value():
    prob = p("C", "multiple_choice", ["1", "2", "\\frac{7}{2}", "4", "5"])
    assert check("C", prob).result == "correct"
    assert check("(c)", prob).result == "correct"
    assert check("\\textbf{(C)}", prob).result == "correct"
    assert check("B", prob).result == "incorrect"
    assert check("3.5", prob).result == "correct"


def test_sets_ignore_order():
    prob = p("\\{-2, 3\\}", "set")
    assert check("3, -2", prob).result == "correct"
    assert check("{3, -2}", prob).result == "correct"
    assert check("3", prob).result == "incorrect"


def test_intervals_respect_endpoints():
    prob = p("[0, 2)", "interval")
    assert check("[0,2)", prob).result == "correct"
    assert check("(0, 2)", prob).result == "incorrect"
    union = p("(-\\infty, -1) \\cup (1, \\infty)", "interval")
    assert check("(-oo,-1) U (1,oo)", union).result == "correct"


def test_unparseable_and_proof():
    assert check("", p("1")).result == "unparseable"
    assert check("anything", p("", "proof")).result == "unverifiable"


def test_agrees_reports_wrong_choice():
    prob = p("B", "multiple_choice", ["1", "2", "3", "4", "5"])
    ok, note = agrees("3", prob)
    assert not ok and "choice C" in note
    assert agrees("2", prob)[0]


@pytest.mark.parametrize(
    "user,truth",
    [
        (r"\frac{x^3}{3}\ln x - \frac{x^3}{9} + C", "x**3*log(x)/3 - x**3/9"),
        ("x^2/2 + c", "x^2/2"),
        ("x^2/2", r"\frac{x^2}{2} + C"),
    ],
)
def test_constant_of_integration_is_ignored(user, truth):
    assert check(user, p(truth)).result == "correct"


def test_capital_c_elsewhere_is_kept():
    # Only a trailing "+ C" is a constant of integration.
    assert check("C + 1", p("2")).result == "incorrect"
