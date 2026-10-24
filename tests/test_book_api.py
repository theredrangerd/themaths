import pytest
from fastapi.testclient import TestClient

from app import db

CH = "/api/books/demo/01"


@pytest.fixture
def client(book_env):
    import app.main

    db.init()
    return TestClient(app.main.app)


def open_(client, ref):
    r = client.post(f"{CH}/problems/{ref}/open")
    assert r.status_code == 200, r.text
    return r.json()["attempt_id"]


def test_books_listing(client):
    r = client.get("/api/books")
    assert r.status_code == 200
    assert r.json()[0]["slug"] == "demo"


def test_chapter_payload_has_no_secrets(client):
    r = client.get(CH)
    assert r.status_code == 200
    body = r.text
    for secret in ("secret-solution", "Substitute the value", "Adding gives", "Book hint", "Tutor nudge", "Tutor hint"):
        assert secret not in body
    for p in (b["problem"] for s in r.json()["chapter"]["sections"] for b in s["blocks"] if "problem" in b):
        assert not {"solution", "answer", "hints", "tutor_hints"} & set(p)


def test_open_ref_with_parentheses(client):
    r = client.post(f"{CH}/problems/1.1.1%28b%29/open")
    assert r.status_code == 200
    assert r.json()["problem"]["book_ref"].endswith("1.1.1(b)")
    assert client.post(f"{CH}/problems/9.9/open").status_code == 404


@pytest.mark.parametrize("path", [
    "/api/books/demo/01/img/..%2F..%2Fapp%2Fdb.py",
    "/api/books/demo/01/img/../chapter.yaml",
    "/api/books/demo/01/img/..%2Fchapter.yaml",
    "/api/books/../01/img/fig.png",
    "/api/books/%2E%2E/01/img/fig.png",
    "/api/books/demo/%2E%2E/img/fig.png",
    "/api/books/demo/nope/img/fig.png",
    "/api/books/demo/nope",
    "/api/books/..%2F..",
])
def test_path_traversal_404s(client, path):
    assert client.get(path).status_code == 404


def test_secret_image_needs_unlocked_attempt(client):
    url = f"{CH}/img/sol.png"
    assert client.get(url).status_code == 404
    a = open_(client, "1.1.2")
    assert client.get(url, params={"attempt": a}).status_code == 404
    assert client.post(f"/api/attempts/{a}/solution").status_code == 200
    assert client.get(url, params={"attempt": a}).status_code == 200
    assert client.get(url).status_code == 404
    assert client.get(url, params={"attempt": 9999}).status_code == 404


def test_image_in_public_text_and_solution_is_public(client):
    # fig.png is in the prose, a box and a hint as well as a solution: no attempt needed.
    assert client.get(f"{CH}/img/fig.png").status_code == 200


def test_self_endpoint_conflict_before_reveal(client):
    a = open_(client, "1.1.2")
    assert client.post(f"/api/attempts/{a}/self", json={"got_it": True}).status_code == 409
    client.post(f"/api/attempts/{a}/solution")
    r = client.post(f"/api/attempts/{a}/self", json={"got_it": True})
    assert r.status_code == 200
    assert client.post("/api/attempts/9999/self", json={"got_it": True}).status_code == 404


def test_retry(client):
    a = open_(client, "1.1")
    b = client.post(f"/api/attempts/{a}/retry").json()["attempt_id"]
    assert b != a
    assert client.post("/api/attempts/9999/retry").status_code == 404


def test_statuses_and_summary(client):
    a = open_(client, "1.1")
    assert client.post(f"/api/attempts/{a}/check", json={"answer": "7"}).status_code == 200
    r = client.get(CH).json()
    assert r["statuses"]["1.1"] == "unaided"
    assert r["summary"]["solved"] == 1
    assert r["summary"]["unaided"] == 1
    assert r["summary"]["total"] == 8
    assert r["summary"]["seen"] == 0


def test_broken_content_reports_file(client, copy_book):
    f = copy_book / "demo" / "01" / "1-1.yaml"
    f.write_text("blocks: [unclosed\n  - : :")
    r = client.get(CH)
    assert r.status_code == 500
    assert "1-1.yaml" in r.json()["detail"]


def test_missing_chapter_dir_in_listing_404s(client, copy_book):
    import shutil

    shutil.rmtree(copy_book / "demo" / "01")
    assert client.get("/api/books").status_code == 404
