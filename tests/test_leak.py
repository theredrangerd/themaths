from app import leak

PAIRS = {"answer": "C", "answer_type": "multiple_choice", "choices": ["6", "8", "9", "10", "12"]}


def test_real_leaks_from_qwen_are_caught():
    # Verbatim replies that gave the answer away.
    for text in [
        "This leads to an incorrect count of 8 instead of the correct 9.",
        "Removing it erroneously reduces the count from 9 to 8.",
        "The reference solution correctly counts all 9 factor pairs of 36.",
        "The number of factor pairs of 36 is indeed $9$.",
    ]:
        assert leak.find(text, PAIRS), text


def test_quoted_student_working_is_not_a_leak():
    text = 'You wrote: "36 has 9 positive divisors, so 9 factor pairs." The problem never says $a \\neq b$.'
    assert leak.find(text, PAIRS) == []


def test_other_numbers_and_expressions_are_fine():
    text = "Adding $36$ to both sides gives $(a - 6)(b - 6) = 36$, and $x^9$ is irrelevant."
    assert leak.find(text, PAIRS) == []


def test_letter_leaks_for_multiple_choice():
    assert leak.find("So the answer is (C).", PAIRS)
    assert leak.find("Choice C is right.", PAIRS)


def test_expression_answers_compare_by_value():
    prob = {"answer": "-1/9", "answer_type": "exact_expression", "choices": []}
    assert leak.find("Evaluating gives $-\\frac{1}{9}$.", prob)
    assert leak.find("so the integral equals $\\int_0^1 x^2 \\ln x \\, dx = -\\frac{1}{9}$", prob)
    assert leak.find("Use integration by parts with $u = \\ln x$.", prob) == []


def test_redact_hides_values():
    assert leak.redact("reduces the count from 9 to 8", ["9"]) == "reduces the count from [hidden] to 8"
    assert leak.redact("instead of the correct 9.", ["9"]) == "instead of the correct [hidden]."


def test_proofs_have_no_secret():
    assert leak.find("Since 9 is odd...", {"answer": "n^3-n is divisible by 6", "answer_type": "proof", "choices": []}) == []


def test_hints_that_jump_ahead_are_caught():
    statement = "Find the sum of all positive integers $n$ for which $n^2 - 19n + 99$ is a perfect square."
    working = "Set n^2 - 19n + 99 = k^2. Multiply by 4: 4n^2 - 76n + 396 = 4k^2."
    # Verbatim qwen3:14b hints: 35 only appears after completing the square.
    assert leak.ahead("Solve $y - x = a$, $y + x = b$ for all factor pairs $(a, b)$ of 35.", statement, working) == ["35"]
    assert leak.ahead("What do you notice about $4n^2 - 76n + 396 = 4k^2$?", statement, working) == []
    assert leak.ahead("Try multiplying both sides by 4 and completing the square.", statement, "") == []
