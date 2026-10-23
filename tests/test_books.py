import json

import pytest
import yaml

from app import books


def _ch():
    return books.load_chapter("demo", "01")


def test_loads_fixture_verbatim(book_env):
    ch = _ch()
    assert ch["title"] == "Warm-up Gadgets" and ch["number"] == 1
    assert ch["tags"]["subtopic"] == "Polynomials"
    sec = ch["sections"][0]
    assert sec["blocks"][0]["prose"] == (
        "A gadget has weight $\\frac{a}{b}$ when a # sign is present.\n"
        "Note: the colon-space here must survive, as must \\textbf{this}.\n"
    )
    assert sec["blocks"][1] == {"box": "important", "text": "Every gadget has a weight: no exceptions.\n"}
    p = books.find_problem(ch, "1.1.4")
    assert p["tutor_hints"] and p["covers"] == [] and p["preamble"] == ""


def test_list_books(book_env):
    assert books.list_books() == [
        {"slug": "demo", "title": "Demo Algebra", "chapters": [{"slug": "01", "number": 1, "title": "Warm-up Gadgets"}]}
    ]


def test_public_chapter_hides_secrets(book_env):
    ch = _ch()
    dump = json.dumps(books.public_chapter(ch))
    for needle in (
        "secret-solution", "Secret-solution", "Adding gives a result of 7", "Substitute the value",
        "Tutor nudge", "Book hint", "Tutor hint", '"answer"', '"hints"', '"solution"', '"tutor_hints"',
    ):
        assert needle not in dump
    p = books.public_chapter(ch)["sections"][0]["blocks"][2]["problem"]
    assert set(p) == {"ref", "kind", "preamble", "statement", "answer_type", "solution_by"}


def test_public_rewrites_images_in_statements(book_env):
    ch = _ch()
    p = books.find_problem(ch, "1.1")
    p["statement"] += "\n![](img/fig.png)"
    out = books.public_chapter(ch)["sections"][0]["blocks"][2]["problem"]["statement"]
    assert "(/api/books/demo/01/img/fig.png)" in out


def test_ladder_book_hints_first(book_env):
    ch = _ch()
    rungs, n = books.ladder(books.find_problem(ch, "1.1"))
    assert n == 2 and rungs[2] == "Tutor nudge one: check your arithmetic."
    assert "7" in rungs[1]
    rungs, n = books.ladder(books.find_problem(ch, "1.1.4"))
    assert rungs == ["Book hint alpha.", "Book hint beta.", "Book hint gamma."] and n == 3


def test_problem_row_statuses(book_env):
    ch = _ch()
    row = lambda r: books.problem_row(ch, books.find_problem(ch, r))
    assert row("1.1.4")["verification_status"] == "tutor_solution"
    assert row("1.1.3")["verification_status"] == "not_applicable"
    assert row("1.1")["verification_status"] == "from_book"
    r = row("1.1.1(b)")
    assert r["statement"].startswith("Let a widget have height")
    assert "\n\nFind the perimeter" in r["statement"]
    assert r["source"] == "book" and r["book_ref"] == "demo/01/1.1.1(b)"
    assert (r["track"], r["topic"], r["subtopic"]) == ("AMC", "Algebra", "Polynomials")
    assert row("1.1")["book_hints"] == 2 and len(row("1.1")["hints"]) == 3
    assert "(/api/books/demo/01/img/fig.png)" in row("1.1.2")["solution"]


def _edit(root, fn):
    path = root / "demo" / "01" / "1-1.yaml"
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data))


def _problems(data):
    return [b["problem"] for b in data["blocks"] if "problem" in b]


def test_hash_changes_with_content(copy_book):
    before = books.problem_row(_ch(), books.find_problem(_ch(), "1.1"))["content_hash"]
    again = books.problem_row(_ch(), books.find_problem(_ch(), "1.1"))["content_hash"]
    assert before == again
    _edit(copy_book, lambda d: _problems(d)[0].update(statement="A different statement."))
    ch = _ch()
    assert books.problem_row(ch, books.find_problem(ch, "1.1"))["content_hash"] != before


def test_secret_images(book_env):
    assert books.secret_images(_ch()) == {"fig.png"}


@pytest.mark.parametrize("which", ["dup", "type", "statement"])
def test_bad_content_names_file_and_ref(copy_book, which):
    def bad(d):
        ps = _problems(d)
        if which == "dup":
            ps[1]["ref"] = "1.1"
        elif which == "type":
            ps[1]["answer_type"] = "banana"
        else:
            del ps[1]["statement"]

    _edit(copy_book, bad)
    with pytest.raises(books.BookError) as e:
        _ch()
    msg = str(e.value)
    assert "1-1.yaml" in msg
    assert ('problem 1.1:' if which == "dup" else "problem 1.1.1(a):") in msg


def test_malformed_yaml_names_file(copy_book):
    (copy_book / "demo" / "01" / "1-1.yaml").write_text("blocks: [unclosed\n  - : :")
    with pytest.raises(books.BookError, match="1-1.yaml"):
        _ch()


def test_bad_slugs(book_env):
    with pytest.raises(LookupError):
        books.load_chapter("../x", "01")
    with pytest.raises(LookupError):
        books.load_chapter("demo", "99")
    with pytest.raises(LookupError):
        books.image_path("demo", "01", "../../app/db.py")
    with pytest.raises(LookupError):
        books.image_path("demo", "01", "nope.png")
    assert books.image_path("demo", "01", "fig.png").name == "fig.png"
    with pytest.raises(LookupError):
        books.find_problem(_ch(), "9.9")


def test_siblings(book_env):
    assert books.siblings("demo/01/1.1.1(a)") == ["demo/01/1.1.1(b)"]
    assert books.siblings("demo/01/1.1") == []
