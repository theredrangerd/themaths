"""Parse model replies written as plain text under fixed `## Heading` sections.

Writing maths inside JSON forces the model to escape every LaTeX backslash, which it gets
wrong. Plain sections need no escaping; this module splits them deterministically.

Only known headings start a section, so a model-written "## Step 1" inside a solution
stays part of the solution.
"""

import re

DRAFT = ("statement", "track", "topic", "subtopic", "difficulty", "answer_type", "answer", "choices", "verification")
FINISH = ("solution", "hint_1", "hint_2", "hint_3", "distractors")

_ALIASES = {
    "problem": "statement",
    "answer type": "answer_type",
    "type": "answer_type",
    "final answer": "answer",
    "options": "choices",
    "answer choices": "choices",
    "verification code": "verification",
    "code": "verification",
    "hint 1": "hint_1",
    "hint 2": "hint_2",
    "hint 3": "hint_3",
    "wrong answers": "distractors",
}
_HEADING = re.compile(r"^#{1,4}\s*(.+?)\s*:?\s*$")


def _key(heading: str) -> str:
    h = re.sub(r"[*_`]", "", heading).strip().lower()
    h = _ALIASES.get(h, h)
    return h.replace(" ", "_")


def split(text: str, known: tuple[str, ...]) -> dict[str, str]:
    """Map each known section name to its body. Text before the first heading is dropped."""
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m and _key(m.group(1)) in known:
            current = _key(m.group(1))
            out[current] = []
        elif current:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _plain(s: str) -> str:
    """A short value: strip code ticks, surrounding $ and a trailing full stop."""
    s = s.strip().strip("`").strip()
    if s.startswith("$") and s.endswith("$"):
        s = s.strip("$").strip()
    return s.rstrip(".").strip()


def _items(body: str) -> list[str]:
    """Lines of a list: "- x", "1. x", "(A) x" or "A. x" all give x."""
    items = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-*•]|\d+[.)]|\(?[A-E]\)|[A-E][.:])\s*", "", line)
        if line:
            items.append(_plain(line))
    return items


def _code(body: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", body, re.S)
    return (m.group(1) if m else body).strip()


def draft(text: str) -> tuple[dict, list[str]]:
    """Parse a stage 1 reply. Returns (fields, names of missing required sections)."""
    s = split(text, DRAFT)
    fields = {
        "statement": s.get("statement", ""),
        "track": _plain(s.get("track", "")).upper().split()[0] if s.get("track") else "",
        "topic": _plain(s.get("topic", "")),
        "subtopic": _plain(s.get("subtopic", "")),
        "difficulty": _plain(s.get("difficulty", "")),
        "answer_type": _plain(s.get("answer_type", "")).lower().replace(" ", "_").replace("-", "_"),
        "answer": _plain(s.get("answer", "")),
        "choices": _items(s.get("choices", "")),
        "verification": _code(s.get("verification", "")),
    }
    required = ("statement", "track", "topic", "answer_type", "answer", "verification")
    return fields, [k for k in required if not fields[k]]


def finish(text: str) -> tuple[dict, list[str]]:
    """Parse a stage 2 reply. Returns (fields, names of missing required sections)."""
    s = split(text, FINISH)
    fields = {
        "solution": s.get("solution", ""),
        "hints": [s.get(f"hint_{i}", "").strip() for i in (1, 2, 3)],
        "distractors": _items(s.get("distractors", "")),
        "boxed": boxed(s.get("solution", "")),
    }
    missing = ["solution"] if not fields["solution"] else []
    if fields["solution"] and fields["boxed"] is None:
        missing.append("a final answer in \\boxed{} at the end of the solution")
    missing += [f"hint {i}" for i, h in enumerate(fields["hints"], 1) if not h]
    return fields, missing


def boxed(text: str) -> str | None:
    """The contents of the last \\boxed{...} in text, with nested braces matched."""
    start = text.rfind("\\boxed{")
    if start < 0:
        return None
    i = start + len("\\boxed{")
    depth, out = 1, []
    while i < len(text):
        ch = text[i]
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            return "".join(out).strip()
        out.append(ch)
        i += 1
    return None
