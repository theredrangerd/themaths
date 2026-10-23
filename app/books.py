"""Load textbook content from YAML under `books/`.

Files are small and edited by hand, so nothing is cached: a fix shows up on the next request.
Bad content raises BookError naming the file and, where there is one, the problem ref.
Slugs and image names are checked against fixed patterns before any path is built, so a
hostile name can never reach outside the books directory.
"""

import copy
import hashlib
import json
import re
from pathlib import Path

import yaml

from . import config
from .verify import ANSWER_TYPES

SLUG = re.compile(r"^[a-z0-9-]+$")
IMAGE = re.compile(r"^[\w-]+\.(png|jpg|svg)$")
_IMG_LINK = re.compile(r"(!\[[^\]]*\]\()img/([\w-]+\.(?:png|jpg|svg))(\))")

KINDS = ("intext", "exercise", "review", "challenge")
BOXES = ("important", "concept", "warning", "sidenote", "extra")
TYPES = ANSWER_TYPES + ("self",)
PUBLIC_FIELDS = ("ref", "kind", "preamble", "statement", "answer_type", "solution_by")
DEFAULTS = {
    "preamble": "", "hints": [], "solution": "", "solution_by": "book",
    "answer": "", "tutor_hints": [], "covers": [],
}


class BookError(ValueError):
    pass


def _slug(value: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value):
        raise LookupError(f"bad slug: {value!r}")
    return value


def _book_dir(book: str) -> Path:
    d = config.BOOKS_DIR / _slug(book)
    if not d.is_dir():
        raise LookupError(f"no such book: {book}")
    return d


def _chapter_dir(book: str, chapter: str) -> Path:
    d = _book_dir(book) / _slug(chapter)
    if not d.is_dir():
        raise LookupError(f"no such chapter: {book}/{chapter}")
    return d


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BookError(f"{path.name}: file is missing") from None
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise BookError(f"{path.name}: cannot parse ({e})") from None
    if not isinstance(data, dict):
        raise BookError(f"{path.name}: expected a mapping at the top level")
    return data


def _need(data: dict, key: str, kind: type, where: str):
    v = data.get(key)
    if not isinstance(v, kind) or (kind is str and not v.strip()):
        raise BookError(f"{where}: '{key}' must be a non-empty {kind.__name__}" if kind is str
                        else f"{where}: '{key}' must be a {kind.__name__}")
    return v


def _problem(raw, file: str, seen: set) -> dict:
    if not isinstance(raw, dict):
        raise BookError(f"{file}: a problem must be a mapping")
    ref = raw.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise BookError(f"{file}: a problem has no 'ref'")
    where = f"{file}, problem {ref}"
    if ref in seen:
        raise BookError(f"{where}: duplicate ref")
    seen.add(ref)
    p = {**copy.deepcopy(DEFAULTS), **raw}
    _need(p, "statement", str, where)
    _need(p, "kind", str, where)
    _need(p, "answer_type", str, where)
    if p["kind"] not in KINDS:
        raise BookError(f"{where}: kind must be one of {', '.join(KINDS)}")
    if p["answer_type"] not in TYPES:
        raise BookError(f"{where}: unknown answer_type {p['answer_type']!r}")
    if p["solution_by"] not in ("book", "tutor"):
        raise BookError(f"{where}: solution_by must be book or tutor")
    for key in ("preamble", "solution", "answer"):
        if not isinstance(p[key], str):
            raise BookError(f"{where}: '{key}' must be a string (quote numbers)")
    for key in ("hints", "tutor_hints", "covers"):
        if not isinstance(p[key], list) or not all(isinstance(x, str) for x in p[key]):
            raise BookError(f"{where}: '{key}' must be a list of strings")
    if p["answer_type"] not in ("proof", "self") and not p["answer"].strip():
        raise BookError(f"{where}: an answer is required for answer_type {p['answer_type']}")
    return p


def _section(path: Path, seen: set) -> dict:
    data = _read(path)
    file = path.name
    sec = {
        "number": _need(data, "number", str, file),
        "title": _need(data, "title", str, file),
        "star": bool(data.get("star", False)),
        "blocks": [],
    }
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        raise BookError(f"{file}: 'blocks' must be a list")
    for i, b in enumerate(blocks, 1):
        if not isinstance(b, dict):
            raise BookError(f"{file}: block {i} must be a mapping")
        if "problem" in b:
            sec["blocks"].append({"problem": _problem(b["problem"], file, seen)})
        elif "prose" in b:
            sec["blocks"].append({"prose": _need(b, "prose", str, f"{file}, block {i}")})
        elif "box" in b:
            if b["box"] not in BOXES:
                raise BookError(f"{file}, block {i}: box must be one of {', '.join(BOXES)}")
            sec["blocks"].append({"box": b["box"], "text": _need(b, "text", str, f"{file}, block {i}")})
        else:
            raise BookError(f"{file}: block {i} must have prose, box or problem")
    return sec


def list_books() -> list[dict]:
    out = []
    root = config.BOOKS_DIR
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not (d.is_dir() and SLUG.match(d.name) and (d / "book.yaml").exists()):
            continue
        meta = _read(d / "book.yaml")
        chapters = []
        for slug in _need(meta, "chapters", list, f"{d.name}/book.yaml"):
            ch = _read(_chapter_dir(d.name, str(slug)) / "chapter.yaml")
            chapters.append({"slug": str(slug), "number": ch.get("number"), "title": ch.get("title")})
        out.append({"slug": d.name, "title": _need(meta, "title", str, f"{d.name}/book.yaml"), "chapters": chapters})
    return out


def load_chapter(book: str, chapter: str) -> dict:
    d = _chapter_dir(book, chapter)
    meta = _read(d / "chapter.yaml")
    where = f"{book}/{chapter}/chapter.yaml"
    tags = _need(meta, "tags", dict, where)
    files = _need(meta, "sections", list, where)
    seen: set = set()
    sections = []
    for name in files:
        if not isinstance(name, str) or not re.match(r"^[\w.-]+\.yaml$", name):
            raise BookError(f"{where}: bad section file name {name!r}")
        sections.append(_section(d / name, seen))
    ch = {
        "book": book, "slug": chapter,
        "number": meta.get("number"),
        "title": _need(meta, "title", str, where),
        "tags": tags, "sections": sections,
    }
    for _, p in _problems(ch):
        for ref in p["covers"]:
            if ref not in seen:
                raise BookError(f"{where}: problem {p['ref']} covers unknown ref {ref}")
    return ch


def _problems(ch: dict):
    for sec in ch["sections"]:
        for b in sec["blocks"]:
            if "problem" in b:
                yield sec, b["problem"]


def find_problem(ch: dict, ref: str) -> dict:
    for _, p in _problems(ch):
        if p["ref"] == ref:
            return p
    raise LookupError(f"no such problem: {ref}")


def _images(text: str, book: str, chapter: str) -> str:
    return _IMG_LINK.sub(lambda m: f"{m[1]}/api/books/{book}/{chapter}/img/{m[2]}{m[3]}", text)


def public_chapter(ch: dict) -> dict:
    out = {k: v for k, v in ch.items() if k != "sections"}
    out["sections"] = []
    for sec in ch["sections"]:
        blocks = []
        for b in sec["blocks"]:
            if "problem" in b:
                p = {k: b["problem"][k] for k in PUBLIC_FIELDS}
                p["preamble"] = _images(p["preamble"], ch["book"], ch["slug"])
                p["statement"] = _images(p["statement"], ch["book"], ch["slug"])
                blocks.append({"problem": p})
            else:
                b = copy.deepcopy(b)
                for key in ("prose", "text"):
                    if key in b:
                        b[key] = _images(b[key], ch["book"], ch["slug"])
                blocks.append(b)
        out["sections"].append({**sec, "blocks": blocks})
    return out


def ladder(problem: dict) -> tuple[list[str], int]:
    rungs = (problem["hints"][:3] + problem["tutor_hints"])[:3]
    return rungs, min(len(problem["hints"]), 3)


def problem_row(ch: dict, problem: dict) -> dict:
    hints, book_hints = ladder(problem)
    if problem["answer_type"] == "proof":
        status = "not_applicable"
    elif problem["solution_by"] == "tutor":
        status = "tutor_solution"
    else:
        status = "from_book"
    statement = problem["statement"]
    if problem["preamble"].strip():
        statement = problem["preamble"].rstrip("\n") + "\n\n" + statement
    row = {
        "source": "book",
        "book_ref": f"{ch['book']}/{ch['slug']}/{problem['ref']}",
        "statement": statement,
        "hints": [_images(h, ch["book"], ch["slug"]) for h in hints],
        "book_hints": book_hints,
        "solution": _images(problem["solution"], ch["book"], ch["slug"]),
        "answer_type": problem["answer_type"],
        "answer": problem["answer"],
        "track": ch["tags"].get("track"),
        "topic": ch["tags"].get("topic"),
        "subtopic": ch["tags"].get("subtopic"),
        "verification_status": status,
    }
    blob = json.dumps(row, sort_keys=True, ensure_ascii=False)
    row["content_hash"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return row


def secret_images(ch: dict) -> set[str]:
    """Images referenced only in solutions. One also used in any public text, or in a hint, is public."""
    def names(text: str) -> set[str]:
        return {m[2] for m in _IMG_LINK.finditer(text or "")}

    in_solutions: set[str] = set()
    public: set[str] = set()
    for sec in ch["sections"]:
        for b in sec["blocks"]:
            if "problem" not in b:
                public |= names(b.get("prose", "")) | names(b.get("text", ""))
                continue
            p = b["problem"]
            in_solutions |= names(p["solution"])
            for text in (p["preamble"], p["statement"], *p["hints"], *p["tutor_hints"]):
                public |= names(text)
    return in_solutions - public


def image_path(book: str, chapter: str, name: str) -> Path:
    d = _chapter_dir(book, chapter)
    if not isinstance(name, str) or not IMAGE.match(name):
        raise LookupError(f"bad image name: {name!r}")
    path = d / "img" / name
    if not path.is_file():
        raise LookupError(f"no such image: {name}")
    return path


def siblings(book_ref: str) -> list[str]:
    book, chapter, ref = book_ref.split("/", 2)
    p = find_problem(load_chapter(book, chapter), ref)
    return [f"{book}/{chapter}/{r}" for r in p["covers"] if r != ref]
