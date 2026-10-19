"""Deterministic answer checking.

The model never decides on its own whether an answer is right: answers are parsed into SymPy
objects and compared here, according to the problem's answer type.
"""

import random
import re
from dataclasses import dataclass

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
LOCALS = {"e": sp.E, "pi": sp.pi, "i": sp.I, "oo": sp.oo, "inf": sp.oo, "infinity": sp.oo, "ln": sp.log}
ANSWER_TYPES = ("exact_expression", "numeric", "integer", "multiple_choice", "set", "interval", "proof")


class ParseError(ValueError):
    pass


@dataclass
class Verdict:
    result: str  # correct | incorrect | unparseable | unverifiable
    note: str = ""


# Normalisation ----------------------------------------------------------

_WRAPPERS = [(r"^\$\$(.*)\$\$$", 1), (r"^\$(.*)\$$", 1), (r"^\\\((.*)\\\)$", 1), (r"^\\\[(.*)\\\]$", 1)]


def normalise(s: str) -> str:
    s = (s or "").strip()
    for pattern, group in _WRAPPERS:
        m = re.match(pattern, s, re.S)
        if m:
            s = m.group(group).strip()
    s = re.sub(r"^(final\s+)?answer\s*[:=]?\s*", "", s, flags=re.I)
    s = re.sub(r"\\boxed\{(.*)\}$", r"\1", s)
    s = s.rstrip(". ")
    # A single leading "x =" or "f(x) =" is a label, not part of the value.
    m = re.match(r"^[A-Za-z]\w*(\([^()]*\))?\s*=\s*(.+)$", s)
    if m and "=" not in m.group(2):
        s = m.group(2)
    replacements = {
        "\\dfrac": "\\frac", "\\tfrac": "\\frac", "\\left": "", "\\right": "", "\\,": "", "\\;": "",
        "\\!": "", "\\ ": "", "\\cdot": "*", "\\times": "*", "−": "-", "–": "-", "π": "pi", "√": "sqrt",
        "∞": "oo", "\\displaystyle": "",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)
    return s.strip()


def _latex_to_plain(s: str) -> str:
    """Crude LaTeX→Python conversion, used when the LaTeX parser cannot cope."""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"((\1)/(\2))", s)
        s = re.sub(r"\\sqrt\[([^{}\]]*)\]\{([^{}]*)\}", r"((\2)**(1/(\1)))", s)
        s = re.sub(r"\\sqrt\{([^{}]*)\}", r"sqrt(\1)", s)
    s = s.replace("\\pi", "pi").replace("\\infty", "oo").replace("\\ln", "log").replace("\\log", "log")
    s = re.sub(r"\\(sin|cos|tan|sec|csc|cot|arcsin|arccos|arctan|exp)", r"\1", s)
    s = s.replace("^", "**").replace("{", "(").replace("}", ")")
    return s


def _fix_symbols(expr: sp.Basic) -> sp.Basic:
    subs = {sp.Symbol("e"): sp.E, sp.Symbol("i"): sp.I, sp.Symbol("pi"): sp.pi}
    return expr.subs(subs) if hasattr(expr, "subs") else expr


def to_sympy(s: str) -> sp.Basic:
    s = normalise(s)
    if not s:
        raise ParseError("empty answer")
    attempts = []
    if "\\" in s or "{" in s:
        attempts.append(lambda: _parse_latex(s))
        attempts.append(lambda: parse_expr(_latex_to_plain(s), local_dict=LOCALS, transformations=TRANSFORMS))
    else:
        attempts.append(lambda: parse_expr(s, local_dict=LOCALS, transformations=TRANSFORMS))
        attempts.append(lambda: _parse_latex(s))
    for attempt in attempts:
        try:
            return _fix_symbols(sp.sympify(attempt()))
        except Exception:
            continue
    raise ParseError(f"could not read {s!r}")


def _parse_latex(s: str) -> sp.Basic:
    from sympy.parsing.latex import parse_latex

    return parse_latex(s)


# Comparison -------------------------------------------------------------

def _num(x: sp.Basic) -> complex:
    return complex(sp.N(x, 30))


def _close(a: complex, b: complex, rel: float) -> bool:
    return abs(a - b) <= rel * max(1.0, abs(b))


def equal(a: sp.Basic, b: sp.Basic, rel: float = 1e-9) -> bool:
    """Mathematical equality: symbolic first, then numeric evaluation."""
    if isinstance(a, sp.logic.boolalg.Boolean) or isinstance(b, sp.logic.boolalg.Boolean):
        return a == b
    try:
        if sp.simplify(a - b) == 0:
            return True
    except Exception:
        pass
    symbols = sorted((a.free_symbols | b.free_symbols), key=str)
    try:
        if not symbols:
            return _close(_num(a), _num(b), rel)
        rng = random.Random(0)
        for _ in range(6):
            point = {s: sp.Rational(rng.randint(-300, 300), 97) + sp.Rational(1, 7) for s in symbols}
            va, vb = _num(a.subs(point)), _num(b.subs(point))
            if not _close(va, vb, max(rel, 1e-8)):
                return False
        return True
    except Exception:
        return False


def _sig_figs(raw: str) -> int | None:
    m = re.fullmatch(r"\s*-?(\d*)\.?(\d*)\s*", raw)
    if not m or "." not in raw:
        return None
    digits = (m.group(1) + m.group(2)).lstrip("0")
    return len(digits) or None


def _matches_rounded(user_raw: str, truth: sp.Basic) -> int | None:
    """If the user gave a decimal with >= 3 significant figures that the true value rounds to, return the s.f."""
    sf = _sig_figs(normalise(user_raw))
    if not sf or sf < 3:
        return None
    try:
        t = float(sp.N(truth))
        u = float(normalise(user_raw))
    except Exception:
        return None
    if t == 0:
        return None
    return sf if float(f"{t:.{sf}g}") == u else None


def _split_top_level(s: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _strip_set_wrapper(s: str) -> str:
    s = normalise(s).replace("\\{", "{").replace("\\}", "}")
    s = re.sub(r"^[A-Za-z]\s*=\s*", "", s)
    if len(s) >= 2 and s[0] in "{[(" and s[-1] in "}])":
        inner = s[1:-1]
        # Only strip if the brackets enclose the whole thing.
        depth = 0
        for ch in inner:
            depth += ch in "([{"
            depth -= ch in ")]}"
            if depth < 0:
                return s
        return inner
    return s


def _set_equal(user: str, truth: str) -> bool:
    u = [to_sympy(x) for x in _split_top_level(_strip_set_wrapper(user))]
    t = [to_sympy(x) for x in _split_top_level(_strip_set_wrapper(truth))]
    if len(u) != len(t):
        return False
    remaining = list(t)
    for x in u:
        for i, y in enumerate(remaining):
            if equal(x, y):
                remaining.pop(i)
                break
        else:
            return False
    return True


_INTERVAL = re.compile(r"^\s*([\[(])\s*(.+?)\s*,\s*(.+?)\s*([\])])\s*$")


def parse_interval(s: str) -> sp.Set:
    s = normalise(s)
    s = s.replace("\\cup", "U").replace("∪", "U")
    pieces = [p for p in re.split(r"\s+U\s+|\s*U\s*(?=[\[(])", s) if p.strip()]
    sets = []
    for piece in pieces:
        m = _INTERVAL.match(piece)
        if not m:
            raise ParseError(f"could not read interval {piece!r}")
        lo, hi = to_sympy(m.group(2)), to_sympy(m.group(3))
        sets.append(sp.Interval(lo, hi, left_open=m.group(1) == "(", right_open=m.group(4) == ")"))
    return sp.Union(*sets)


def _choice_letter(s: str) -> str | None:
    s = normalise(s)
    s = re.sub(r"\\textbf\{\s*\(?([A-Ea-e])\)?\s*\}", r"\1", s)
    m = re.fullmatch(r"\(?\s*([A-Ea-e])\s*\)?", s)
    return m.group(1).upper() if m else None


def check(user_answer: str, problem: dict) -> Verdict:
    """Mark a user's final answer against the stored answer for a problem."""
    kind = problem["answer_type"]
    truth = problem["answer"]
    if kind == "proof":
        return Verdict("unverifiable", "Proofs can't be machine-checked; the tutor will review your working.")
    if not normalise(user_answer):
        return Verdict("unparseable", "Enter a final answer to check.")
    try:
        if kind == "multiple_choice":
            letter = _choice_letter(user_answer)
            want = _choice_letter(truth)
            if letter:
                return Verdict("correct" if letter == want else "incorrect")
            choices = problem.get("choices") or []
            idx = "ABCDE".index(want) if want else -1
            if 0 <= idx < len(choices):
                ok = equal(to_sympy(user_answer), to_sympy(choices[idx]))
                return Verdict("correct" if ok else "incorrect", "Matched by value rather than letter." if ok else "")
            return Verdict("unparseable", "Answer with the letter of your choice, A to E.")
        if kind == "set":
            return Verdict("correct" if _set_equal(user_answer, truth) else "incorrect")
        if kind == "interval":
            return Verdict("correct" if parse_interval(user_answer) == parse_interval(truth) else "incorrect")

        u, t = to_sympy(user_answer), to_sympy(truth)
        if kind == "integer":
            ok = equal(u, t) and sp.N(u).is_integer is not False
            return Verdict("correct" if ok else "incorrect")
        if equal(u, t):
            return Verdict("correct")
        if (sf := _matches_rounded(user_answer, t)) is not None:
            return Verdict("correct", f"Accepted to {sf} significant figures; the exact value is ${sp.latex(t)}$.")
        return Verdict("incorrect")
    except ParseError:
        return Verdict("unparseable", "That answer couldn't be read. Try plain form, e.g. 3/4, 2*sqrt(3), or \\frac{3}{4}.")
    except Exception:
        return Verdict("unparseable", "That answer couldn't be compared. Try writing it in a simpler form.")


def agrees(computed: str, problem: dict) -> tuple[bool, str]:
    """Does the verification snippet's computed value agree with the stated answer?"""
    kind = problem["answer_type"]
    try:
        if kind == "multiple_choice":
            want = _choice_letter(problem["answer"])
            choices = problem.get("choices") or []
            if _choice_letter(computed):
                return _choice_letter(computed) == want, ""
            if want is None or "ABCDE".index(want) >= len(choices):
                return False, "answer letter does not match any choice"
            ok = equal(to_sympy(computed), to_sympy(choices["ABCDE".index(want)]))
            if not ok:
                # Report which choice, if any, the computation actually matched.
                for letter, c in zip("ABCDE", choices):
                    try:
                        if equal(to_sympy(computed), to_sympy(c)):
                            return False, f"computation gives choice {letter}, not {want}"
                    except Exception:
                        pass
            return ok, ""
        if kind == "set":
            return _set_equal(computed, problem["answer"]), ""
        if kind == "interval":
            return parse_interval(computed) == parse_interval(problem["answer"]), ""
        return equal(to_sympy(computed), to_sympy(problem["answer"])), ""
    except Exception as exc:
        return False, f"could not compare: {exc}"
