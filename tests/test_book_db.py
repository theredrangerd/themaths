import sqlite3

import pytest

from app import books, db

OLD_SCHEMA_PROBLEMS = """
CREATE TABLE problems (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    source TEXT NOT NULL,            -- generated | screenshot
    request TEXT,
    statement TEXT NOT NULL,
    choices TEXT NOT NULL DEFAULT '[]',
    track TEXT NOT NULL,
    topic TEXT NOT NULL,
    subtopic TEXT,
    difficulty TEXT,
    answer_type TEXT NOT NULL,
    answer TEXT NOT NULL,
    verification TEXT,
    verification_status TEXT NOT NULL, -- verified | unverified | not_applicable
    verification_note TEXT,
    hints TEXT NOT NULL DEFAULT '[]',
    solution TEXT NOT NULL,
    flagged INTEGER NOT NULL DEFAULT 0,
    model TEXT
);
"""


def row(ref="1.1"):
    ch = books.load_chapter("demo", "01")
    return books.problem_row(ch, books.find_problem(ch, ref))


def test_upsert_is_idempotent(book_env):
    db.init()
    r = row()
    a = db.upsert_book_problem(r)
    b = db.upsert_book_problem(r)
    assert a == b
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM problems").fetchone()[0] == 1
    assert db.problem_by_ref(r["book_ref"])["id"] == a
    assert db.problem_by_ref("demo/01/nope") is None


def test_content_fix_updates_row_keeps_attempts_clears_flag(book_env):
    db.init()
    r = row()
    pid = db.upsert_book_problem(r)
    aid = db.start_attempt(pid)
    db.flag_problem(pid)
    assert db.latest_attempt(pid) == aid
    fixed = {**r, "statement": "Fixed statement", "content_hash": "different"}
    assert db.upsert_book_problem(fixed) == pid
    a, p = db.get_attempt(aid)
    assert p["statement"] == "Fixed statement"
    assert p["flagged"] == 0
    assert p["content_hash"] == "different"
    assert a["problem_id"] == pid


def test_same_hash_does_not_clear_flag(book_env):
    db.init()
    r = row()
    pid = db.upsert_book_problem(r)
    db.flag_problem(pid)
    db.upsert_book_problem(r)
    assert db.problem_by_ref(r["book_ref"])["flagged"] == 1


def test_migrates_old_database(book_env):
    db.DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    conn.executescript(OLD_SCHEMA_PROBLEMS)
    conn.execute(
        "INSERT INTO problems(created_at, source, statement, track, topic, answer_type, answer, "
        "verification_status, solution) VALUES(1, 'generated', 'old', 'AMC', 'Algebra', 'integer', '4', "
        "'verified', 'sol')"
    )
    conn.commit()
    conn.close()
    db.init()
    db.init()  # idempotent
    aid = db.start_attempt(1)
    a, p = db.get_attempt(aid)
    assert p["statement"] == "old"
    assert p["book_ref"] is None and p["book_hints"] == 0 and p["content_hash"] is None
    assert p["hints"] == [] and p["choices"] == []
    # the new unique index works alongside old rows
    assert db.upsert_book_problem(row()) == 2


def test_book_statuses_best_outcome(book_env):
    db.init()
    p1 = db.upsert_book_problem(row("1.1"))
    p2 = db.upsert_book_problem(row("1.1.1(a)"))
    p3 = db.upsert_book_problem(row("1.1.1(b)"))
    p4 = db.upsert_book_problem(row("1.1.2"))
    db.upsert_book_problem(row("1.1.3"))  # never opened

    a = db.start_attempt(p1)
    db.update_attempt(a, outcome="gave_up")
    a = db.start_attempt(p1)
    db.update_attempt(a, outcome="correct", hint_level=1)

    a = db.start_attempt(p2)
    db.update_attempt(a, outcome="correct")

    db.start_attempt(p3)

    a = db.start_attempt(p4)
    db.update_attempt(a, outcome="gave_up", solution_viewed=1)

    s = db.book_statuses("demo/01/")
    assert s == {
        "demo/01/1.1": "solved",
        "demo/01/1.1.1(a)": "unaided",
        "demo/01/1.1.1(b)": "started",
        "demo/01/1.1.2": "seen",
    }
    assert db.book_statuses("other/") == {}


def test_book_reports(book_env):
    db.init()
    r = row()
    pid = db.upsert_book_problem(r)
    aid = db.start_attempt(pid)
    db.add_event(aid, "flag", "Verdict: flawed. ...", meta={"reason": "typo in the statement"})
    gid = db.insert_problem({"source": "generated", "statement": "s", "track": "AMC", "topic": "Algebra",
                             "answer_type": "integer", "answer": "1", "verification_status": "verified",
                             "solution": "x"})
    db.add_event(db.start_attempt(gid), "flag", "not a book one")
    reports = db.book_reports()
    assert len(reports) == 1
    assert reports[0]["book_ref"] == r["book_ref"]
    assert reports[0]["reason"] == "typo in the statement"
    assert "created_at" in reports[0]
