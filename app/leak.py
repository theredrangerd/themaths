"""Catch tutor replies that give away the final answer.

Small models are poor at not saying something they know: asked never to reveal the answer,
they still write "8 instead of the correct 9". So replies that the model writes while
knowing the answer are checked here before the student sees them.

A leak is the answer's value appearing in the tutor's own words: as a plain number, or as a
maths expression equal to it. Text the tutor quotes from the student's working is ignored.
"""

import re

from . import verify

_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|'[^'\n]{6,}'")
_MATH = re.compile(r"\$\$(.+?)\$\$|\$([^$]+?)\$", re.S)
# A number in prose; a full stop after it ends the sentence unless a digit follows.
_NUMBER = re.compile(r"(?<![\w^_{])(?<!\d\.)-?\d+(?:\.\d+)?(?![\w^_}])(?!\.\d)")


def secret_values(problem: dict) -> list:
    """The values that would give the answer away, as SymPy objects."""
    answer = problem["answer"]
    if problem["answer_type"] == "proof":
        return []
    if problem["answer_type"] == "multiple_choice":
        letter = verify._choice_letter(answer)
        choices = problem.get("choices") or []
        if not letter or "ABCDE".index(letter) >= len(choices):
            return []
        answer = choices["ABCDE".index(letter)]
    if problem["answer_type"] in ("set", "interval"):
        return []  # Too structured to spot reliably in prose; left to the prompt.
    try:
        return [verify.to_sympy(answer)]
    except Exception:
        return []


def _candidates(text: str) -> list[str]:
    text = _QUOTED.sub(" ", text)
    found = []
    for m in _MATH.finditer(text):
        found.append((m.group(1) or m.group(2)).strip())
    prose = _MATH.sub(" ", text)
    found += _NUMBER.findall(prose)
    return found


def find(text: str, problem: dict) -> list[str]:
    """Snippets of text that state the answer's value. Empty if none."""
    secrets = secret_values(problem)
    leaks = []
    for cand in _candidates(text):
        # An equation or inequality is a statement, not a value; check its right-hand side.
        rhs = re.split(r"=|\\le|\\ge|<|>", cand)[-1].strip()
        for value in {cand, rhs}:
            try:
                v = verify.to_sympy(value)
            except Exception:
                continue
            if v.free_symbols:
                continue
            if any(verify.equal(v, s) for s in secrets):
                leaks.append(cand)
                break
    if problem["answer_type"] == "multiple_choice":
        letter = verify._choice_letter(problem["answer"])
        pattern = rf"\({letter}\)|\b(?:option|choice|answer) \(?{letter}\)?\b"
        if letter and re.search(pattern, _QUOTED.sub(" ", text), re.I):
            leaks.append(f"({letter})")
    return leaks


def redact(text: str, leaks: list[str]) -> str:
    """Last resort: hide each leaked value."""
    for snippet in sorted(set(leaks), key=len, reverse=True):
        text = re.sub(rf"(?<![\w.]){re.escape(snippet)}(?!\w)(?!\.\d)", "[hidden]", text)
    return text


def ahead(hint: str, statement: str, working: str) -> list[str]:
    """Numbers a hint introduces that appear in neither the problem nor the student's working.

    A hint that says "factor pairs of 35" when the student hasn't reached 35 does the step
    for them. Small integers (0 to 10) are allowed: "multiply by 2" is ordinary phrasing.
    """
    known = set(_NUMBER.findall(statement)) | set(_NUMBER.findall(working))
    found = []
    for n in _NUMBER.findall(_QUOTED.sub(" ", hint)):
        if n in known or n in found:
            continue
        try:
            if float(n).is_integer() and abs(float(n)) <= 10:
                continue
        except ValueError:
            pass
        found.append(n)
    return found
