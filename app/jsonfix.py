"""Repair LaTeX backslashes in model-written JSON.

Models often write `\\frac` inside a JSON string as `\frac`, which JSON reads as a form feed
followed by "rac". Before parsing, double any backslash that starts a LaTeX command or is
not a valid JSON escape.
"""

import re

# LaTeX commands whose first letter collides with a JSON escape (\b \f \n \r \t).
_COLLIDING = re.compile(
    r"(b(?:eta|inom|ar|egin|ig|Big|mod|oxed|f|ot|ullet|ackslash|igcup|igcap|igl|igr|reve|ecause)"
    r"|f(?:rac|orall|lat|latten|rak)"
    r"|n(?:eq|e|abla|u|ot|otin|eg|mid|ewline|exists|i|parallel|leq|geq|subseteq)"
    r"|r(?:ight|ho|angle|floor|ceil|ightarrow|m|Vert|vert|brace|brack)"
    r"|t(?:imes|ext|extbf|extit|extrm|heta|an|anh|frac|o|op|au|riangle|ilde|herefore|binom|riangleq|extstyle))"
    r"(?![A-Za-z])"
)


def repair(raw: str) -> str:
    out = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = raw[i + 1] if i + 1 < n else ""
        if nxt == "\\":
            out.append("\\\\")
            i += 2
        elif nxt in "bfnrt" and _COLLIDING.match(raw, i + 1):
            out.append("\\\\")
            i += 1
        elif nxt in '"/bfnrt':
            out.append("\\" + nxt)
            i += 2
        elif nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", raw[i + 2 : i + 6]):
            out.append(raw[i : i + 6])
            i += 6
        else:
            out.append("\\\\")
            i += 1
    return "".join(out)
