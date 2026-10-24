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
_PLAIN = re.compile(r"(?<![\w^_{])(?<!\d\.)-?\d+(?:\.\d+)?(?![\w^_}])(?!\.\d)")
# The same, but a plain fraction such as -5/2 is one value, not -5 and 2.
_NUMBER = re.compile(r"(?<![\w^_{])(?<!\d\.)-?(?:\d+/\d+(?![\w^_}/])|\d+(?:\.\d+)?(?![\w^_}])(?!\.\d))")
# Set elements that are integers this small are not guarded one by one: "degree 3" or
# "2 roots" is ordinary phrasing. Naming every element still counts (see find).
_SMALL = 10


def _set_elements(problem: dict) -> list:
    """A set answer's distinct numeric elements, as SymPy objects."""
    if problem["answer_type"] != "set":
        return []
    out = []
    for part in verify._split_top_level(verify._strip_set_wrapper(problem["answer"])):
        try:
            v = verify.to_sympy(part)
        except Exception:
            continue
        if v.free_symbols or any(verify.equal(v, e) for e in out):
            continue
        out.append(v)
    return out


def _small_integer(v) -> bool:
    return bool(v.is_integer) and abs(int(v)) <= _SMALL


def secret_values(problem: dict) -> list:
    """The values that would give the answer away on their own, as SymPy objects."""
    answer = problem["answer"]
    if problem["answer_type"] in ("proof", "self"):
        return []
    if problem["answer_type"] == "set":
        return [v for v in _set_elements(problem) if not _small_integer(v)]
    if problem["answer_type"] == "multiple_choice":
        letter = verify._choice_letter(answer)
        choices = problem.get("choices") or []
        if not letter or "ABCDE".index(letter) >= len(choices):
            return []
        answer = choices["ABCDE".index(letter)]
    if problem["answer_type"] == "interval":
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


def _values(text: str) -> list[tuple[str, object]]:
    """Each value stated in text, with the snippet that states it."""
    out = []
    for cand in _candidates(text):
        # An equation or inequality is a statement, not a value; check its right-hand side too.
        rhs = re.split(r"=|\\le|\\ge|<|>", cand)[-1].strip()
        for value in dict.fromkeys((cand, rhs)):
            try:
                v = verify.to_sympy(value)
            except Exception:
                continue
            if not v.free_symbols:
                out.append((cand, v))
    return out


def find(text: str, problem: dict, known: str = "") -> list[str]:
    """Snippets of text that state the answer's value. Empty if none.

    known is what the student has written themselves (working, answer): values in it are
    theirs already, so repeating them gives nothing away.
    """
    secrets = secret_values(problem)
    elements = _set_elements(problem)
    if not secrets and not elements and problem["answer_type"] != "multiple_choice":
        return []
    theirs = [v for _, v in _values(known)] if known.strip() else []

    def is_theirs(v) -> bool:
        return any(verify.equal(v, k) for k in theirs)

    stated = _values(text)
    leaks = []
    for cand, v in stated:
        if any(verify.equal(v, s) for s in secrets) and not is_theirs(v):
            leaks.append(cand)
    if elements:
        # A set of small integers: naming every element gives the answer away.
        named = [(cand, v) for cand, v in stated if any(verify.equal(v, e) for e in elements)]
        if all(any(verify.equal(v, e) for _, v in named) for e in elements):
            leaks += [cand for cand, v in named if not is_theirs(v) and cand not in leaks]
    if problem["answer_type"] == "multiple_choice":
        letter = verify._choice_letter(problem["answer"])
        pattern = rf"\({letter}\)|\b(?:option|choice|answer) \(?{letter}\)?\b"
        if letter and re.search(pattern, _QUOTED.sub(" ", text), re.I):
            leaks.append(f"({letter})")
    return list(dict.fromkeys(leaks))


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
    known = set(_PLAIN.findall(statement)) | set(_PLAIN.findall(working))
    found = []
    for n in _PLAIN.findall(_QUOTED.sub(" ", hint)):
        if n in known or n in found:
            continue
        try:
            if float(n).is_integer() and abs(float(n)) <= 10:
                continue
        except ValueError:
            pass
        found.append(n)
    return found
