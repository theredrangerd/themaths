import json
import sqlite3
import time
from contextlib import contextmanager

from .config import DATA_DIR, DB_PATH, DEFAULT_SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
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
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    problem_id INTEGER NOT NULL REFERENCES problems(id),
    started_at REAL NOT NULL,
    finished_at REAL,
    outcome TEXT,                    -- correct | incorrect | gave_up | flagged | NULL (in progress)
    submissions INTEGER NOT NULL DEFAULT 0,
    hint_level INTEGER NOT NULL DEFAULT 0,
    solution_viewed INTEGER NOT NULL DEFAULT 0,
    working TEXT NOT NULL DEFAULT '',
    final_answer TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id),
    created_at REAL NOT NULL,
    kind TEXT NOT NULL,              -- hint | solution | check | analysis | explain | flag
    level INTEGER,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with tx() as c:
        c.executescript(SCHEMA)


# Settings ---------------------------------------------------------------

def get_settings() -> dict:
    with tx() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: json.loads(r["value"]) for r in rows}
    return {**DEFAULT_SETTINGS, **stored}


def put_settings(values: dict) -> dict:
    with tx() as c:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, json.dumps(v)),
                )
    return get_settings()


# Problems and attempts --------------------------------------------------

def insert_problem(p: dict) -> int:
    with tx() as c:
        cur = c.execute(
            """INSERT INTO problems(created_at, source, request, statement, choices, track, topic,
               subtopic, difficulty, answer_type, answer, verification, verification_status,
               verification_note, hints, solution, model)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(), p["source"], p.get("request"), p["statement"], json.dumps(p.get("choices") or []),
                p["track"], p["topic"], p.get("subtopic"), p.get("difficulty"), p["answer_type"], p["answer"],
                p.get("verification"), p["verification_status"], p.get("verification_note"),
                json.dumps(p.get("hints") or []), p["solution"], p.get("model"),
            ),
        )
        return cur.lastrowid


def start_attempt(problem_id: int) -> int:
    with tx() as c:
        cur = c.execute("INSERT INTO attempts(problem_id, started_at) VALUES(?, ?)", (problem_id, time.time()))
        return cur.lastrowid


def get_attempt(attempt_id: int) -> tuple[dict, dict] | None:
    with tx() as c:
        a = c.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if not a:
            return None
        p = c.execute("SELECT * FROM problems WHERE id = ?", (a["problem_id"],)).fetchone()
    problem = dict(p)
    problem["choices"] = json.loads(problem["choices"])
    problem["hints"] = json.loads(problem["hints"])
    return dict(a), problem


def update_attempt(attempt_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE attempts SET {cols} WHERE id = ?", (*fields.values(), attempt_id))


def flag_problem(problem_id: int) -> None:
    with tx() as c:
        c.execute("UPDATE problems SET flagged = 1 WHERE id = ?", (problem_id,))


def add_event(attempt_id: int, kind: str, content: str, level: int | None = None, meta: dict | None = None) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO events(attempt_id, created_at, kind, level, content, meta) VALUES(?,?,?,?,?,?)",
            (attempt_id, time.time(), kind, level, content, json.dumps(meta or {})),
        )


def get_events(attempt_id: int) -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM events WHERE attempt_id = ? ORDER BY id", (attempt_id,)).fetchall()
    return [{**dict(r), "meta": json.loads(r["meta"])} for r in rows]


def recent_statements(track: str | None, topic: str | None, limit: int = 6) -> list[str]:
    q, args = "SELECT statement FROM problems WHERE 1=1", []
    if track:
        q += " AND track = ?"
        args.append(track)
    if topic:
        q += " AND topic = ?"
        args.append(topic)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with tx() as c:
        return [r["statement"] for r in c.execute(q, args).fetchall()]


def history(limit: int = 200) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            """SELECT a.id AS attempt_id, a.started_at, a.finished_at, a.outcome, a.hint_level,
                      a.solution_viewed, a.submissions, p.statement, p.track, p.topic, p.subtopic,
                      p.difficulty, p.source, p.flagged
               FROM attempts a JOIN problems p ON p.id = a.problem_id
               ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def engaged_attempts() -> list[dict]:
    """Attempts the user actually worked on: any check, hint, solution view, or flag."""
    with tx() as c:
        rows = c.execute(
            """SELECT a.*, p.track, p.topic, p.subtopic, p.difficulty, p.flagged
               FROM attempts a JOIN problems p ON p.id = a.problem_id
               WHERE a.submissions > 0 OR a.hint_level > 0 OR a.solution_viewed = 1 OR a.outcome IS NOT NULL
               ORDER BY a.started_at"""
        ).fetchall()
    return [dict(r) for r in rows]


def export_rows() -> list[dict]:
    with tx() as c:
        rows = c.execute(
            """SELECT a.id AS attempt_id, a.started_at, a.finished_at, a.outcome, a.submissions,
                      a.hint_level, a.solution_viewed, a.working, a.final_answer,
                      p.id AS problem_id, p.source, p.track, p.topic, p.subtopic, p.difficulty,
                      p.answer_type, p.answer, p.verification_status, p.flagged, p.statement, p.solution
               FROM attempts a JOIN problems p ON p.id = a.problem_id ORDER BY a.id"""
        ).fetchall()
    return [dict(r) for r in rows]
