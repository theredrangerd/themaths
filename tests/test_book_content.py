"""Checks on the real transcribed book under config.BOOKS_DIR (not the fixtures).

Skipped unless Chapter 7 of Intermediate Algebra is present. If it is present but
broken, one failing test_chapter_loads shows the BookError instead of a collection error.
"""

import json
import re

import pytest

from app import books, config, verify

BOOK, CHAPTER = "intermediate-algebra", "07"
pytestmark = pytest.mark.skipif(
    not (config.BOOKS_DIR / BOOK / CHAPTER / "chapter.yaml").exists(),
    reason="Intermediate Algebra chapter 7 is not transcribed",
)

FORBIDDEN = ("Hide Solution", "Reset", "Your Submission", "You may type", "Preview: Solution")
IMG = re.compile(r"!\[[^\]]*\]\(img/([^)\s]+)\)")


def _load():
    try:
        return books.load_chapter(BOOK, CHAPTER), None
    except Exception as e:  # BookError, LookupError, anything a broken file can cause
        return None, e


CH, LOAD_ERROR = _load()
PROBLEMS = [p for _, p in books._problems(CH)] if CH else []
BY_ID = [pytest.param(p, id=p["ref"]) for p in PROBLEMS]


def test_chapter_loads():
    if LOAD_ERROR is not None:
        pytest.fail(f"{type(LOAD_ERROR).__name__}: {LOAD_ERROR}", pytrace=False)


@pytest.mark.parametrize("p", BY_ID)
def test_answer_marks_itself_correct(p):
    if p["answer_type"] in ("proof", "self"):
        pytest.skip("not machine-checked")
    row = books.problem_row(CH, p)
    v = verify.check(p["answer"], row)
    assert v.result == "correct", f"{p['ref']}: answer {p['answer']!r} gave {v.result}: {v.message}"


@pytest.mark.parametrize("p", BY_ID)
def test_three_rungs(p):
    assert len(books.ladder(p)[0]) == 3


@pytest.mark.parametrize("p", BY_ID)
def test_images_exist(p):
    texts = [p["preamble"], p["statement"], p["solution"], *p["hints"], *p["tutor_hints"]]
    for name in (n for t in texts for n in IMG.findall(t)):
        books.image_path(BOOK, CHAPTER, name)


def test_section_images_exist():
    if CH is None:
        pytest.skip("chapter did not load")
    for sec in CH["sections"]:
        for b in sec["blocks"]:
            for name in IMG.findall(b.get("prose", "") + b.get("text", "")):
                books.image_path(BOOK, CHAPTER, name)


def test_no_interface_text():
    if CH is None:
        pytest.skip("chapter did not load")
    dump = json.dumps(CH, ensure_ascii=False)
    found = [s for s in FORBIDDEN if s in dump]
    assert not found, f"interface text left in the chapter: {found}"
