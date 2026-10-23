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
    model TEXT,
    book_ref TEXT,                   -- book/chapter/ref for book problems
    book_hints INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT
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


# Columns added after the first release; init() adds them to older databases.
NEW_PROBLEM_COLUMNS = {
    "book_ref": "TEXT",
    "book_hints": "INTEGER NOT NULL DEFAULT 0",
    "content_hash": "TEXT",
}


def init() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(problems)")}
        for col, decl in NEW_PROBLEM_COLUMNS.items():
            if col not in have:
                c.execute(f"ALTER TABLE problems ADD COLUMN {col} {decl}")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS problems_book_ref ON problems(book_ref)")


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


BOOK_CONTENT_FIELDS = (
    "statement", "hints", "book_hints", "solution", "answer_type", "answer",
    "track", "topic", "subtopic", "verification_status", "content_hash",
)


def upsert_book_problem(row: dict) -> int:
    """Insert a book problem, or update it in place when its content changed. Attempts are untouched."""
    with tx() as c:
        cur = c.execute("SELECT id, content_hash FROM problems WHERE book_ref = ?", (row["book_ref"],)).fetchone()
        if cur is None:
            cur = c.execute(
                """INSERT INTO problems(created_at, source, statement, track, topic, subtopic, answer_type,
                   answer, verification_status, hints, book_hints, solution, book_ref, content_hash)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(), row.get("source", "book"), row["statement"], row["track"], row["topic"],
                    row.get("subtopic"), row["answer_type"], row["answer"], row["verification_status"],
                    json.dumps(row.get("hints") or []), row.get("book_hints", 0), row["solution"],
                    row["book_ref"], row.get("content_hash"),
                ),
            )
            return cur.lastrowid
        if cur["content_hash"] != row.get("content_hash"):
            c.execute(
                """UPDATE problems SET statement = ?, track = ?, topic = ?, subtopic = ?, answer_type = ?,
                   answer = ?, verification_status = ?, hints = ?, book_hints = ?, solution = ?,
                   content_hash = ?, flagged = 0 WHERE id = ?""",
                (
                    row["statement"], row["track"], row["topic"], row.get("subtopic"), row["answer_type"],
                    row["answer"], row["verification_status"], json.dumps(row.get("hints") or []),
                    row.get("book_hints", 0), row["solution"], row.get("content_hash"), cur["id"],
                ),
            )
        return cur["id"]


def problem_by_ref(book_ref: str) -> dict | None:
    with tx() as c:
        p = c.execute("SELECT * FROM problems WHERE book_ref = ?", (book_ref,)).fetchone()
    if not p:
        return None
    problem = dict(p)
    problem["choices"] = json.loads(problem["choices"])
    problem["hints"] = json.loads(problem["hints"])
    return problem


def latest_attempt(problem_id: int) -> int | None:
    with tx() as c:
        r = c.execute("SELECT MAX(id) AS id FROM attempts WHERE problem_id = ?", (problem_id,)).fetchone()
    return r["id"]


def book_statuses(prefix: str) -> dict[str, str]:
    """Best status per book problem under prefix: unaided > solved > seen > started."""
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with tx() as c:
        rows = c.execute(
            """SELECT p.book_ref,
                      MAX(a.outcome = 'correct' AND a.hint_level = 0 AND a.solution_viewed = 0) AS unaided,
                      MAX(a.outcome = 'correct') AS solved,
                      MAX(a.solution_viewed = 1) AS seen
               FROM problems p JOIN attempts a ON a.problem_id = p.id
               WHERE p.book_ref LIKE ? ESCAPE '\\'
               GROUP BY p.id""",
            (like,),
        ).fetchall()
    out = {}
    for r in rows:
        out[r["book_ref"]] = (
            "unaided" if r["unaided"] else "solved" if r["solved"] else "seen" if r["seen"] else "started"
        )
    return out


def book_reports() -> list[dict]:
    with tx() as c:
        rows = c.execute(
            """SELECT p.book_ref, e.meta, e.content, e.created_at
               FROM events e JOIN attempts a ON a.id = e.attempt_id JOIN problems p ON p.id = a.problem_id
               WHERE e.kind = 'flag' AND p.book_ref IS NOT NULL ORDER BY e.id"""
        ).fetchall()
    return [
        {"book_ref": r["book_ref"], "reason": json.loads(r["meta"]).get("reason", r["content"]),
         "created_at": r["created_at"]}
        for r in rows
    ]


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


def recent_subtopics(track: str | None, topic: str | None, limit: int = 6) -> list[str]:
    q, args = "SELECT DISTINCT subtopic FROM (SELECT subtopic FROM problems WHERE subtopic != ''", []
    if track:
        q += " AND track = ?"
        args.append(track)
    if topic:
        q += " AND topic = ?"
        args.append(topic)
    q += " ORDER BY id DESC LIMIT ?)"
    args.append(limit)
    with tx() as c:
        return [r["subtopic"] for r in c.execute(q, args).fetchall()]


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
