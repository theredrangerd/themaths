import asyncio

import pytest

from app import db, tutor


def run(gen):
    async def go():
        return [e async for e in gen]
    return asyncio.run(go())


@pytest.fixture
def no_model(book_env, monkeypatch):
    db.init()
    calls = []

    async def stub(model, messages, think, out, show=True, **kw):
        calls.append(messages)
        out.append("MODEL REPLY")
        return
        yield

    monkeypatch.setattr(tutor, "_stream", stub)
    return calls


def open_(ref):
    return tutor.open_book_problem("demo", "01", ref)["attempt_id"]


def test_open_resumes_latest_attempt(no_model):
    a = open_("1.1")
    assert open_("1.1") == a
    b = tutor.retry(a)["attempt_id"]
    assert b != a
    assert open_("1.1") == b


def test_book_hints_verbatim_even_with_working(no_model):
    a = open_("1.1")
    events = run(tutor.hint(a, "I tried x = 7"))
    assert events[-1]["text"] == "Substitute the value of $x$ into the expression."
    assert events[-1]["state"]["events"][-1]["meta"] == {"book": True}
    assert no_model == []
    run(tutor.hint(a, "I tried x = 7"))  # level 2: book hint naming 7
    assert no_model == []
    run(tutor.hint(a, "I tried x = 7"))  # level 3: model
    assert len(no_model) == 1


def test_book_hint_not_rewritten_when_it_names_answer(no_model):
    a = open_("1.1")
    run(tutor.hint(a, ""))
    events = run(tutor.hint(a, ""))
    assert events[-1]["text"] == "Adding gives a result of 7, which you can now state."
    assert no_model == []


def test_self_problem_reveal_then_mark(no_model):
    a = open_("1.1.2")
    with pytest.raises(ValueError):
        tutor.self_mark(a, True)
    run(tutor.solution(a))
    state = tutor.attempt_state(a)
    assert state["attempt"]["outcome"] is None
    assert state["attempt"]["solution_viewed"] == 1
    state = tutor.self_mark(a, True)
    assert state["attempt"]["outcome"] == "correct"
    assert state["attempt"]["solution_viewed"] == 1


def test_self_mark_got_it_false(no_model):
    a = open_("1.1.2")
    run(tutor.solution(a))
    assert tutor.self_mark(a, False)["attempt"]["outcome"] == "gave_up"


def test_shared_solution_marks_siblings_seen(no_model):
    a = open_("1.1.1(a)")
    run(tutor.solution(a))
    assert db.book_statuses("demo/01/")["demo/01/1.1.1(b)"] == "seen"


def test_solution_images_carry_attempt(no_model):
    a = open_("1.1.2")
    events = run(tutor.solution(a))
    assert f"/api/books/demo/01/img/fig.png?attempt={a}" in events[-1]["text"]
    assert f"?attempt={a}" in tutor.attempt_state(a)["events"][-1]["content"]


def test_public_problem_has_book_fields(no_model):
    p = tutor.open_book_problem("demo", "01", "1.1")["problem"]
    assert p["book_ref"] == "demo/01/1.1" and p["book_hints"] == 2


def test_flagged_book_problem_listed_in_reports(no_model):
    a = open_("1.1")
    run(tutor.flag(a, "typo in the statement"))
    reports = db.book_reports()
    assert [(r["book_ref"], r["reason"]) for r in reports] == [("demo/01/1.1", "typo in the statement")]


def test_self_unverifiable_and_no_secrets():
    from app import leak, verify
    p = {"answer_type": "self", "answer": ""}
    assert verify.check("x", p).result == "unverifiable"
    assert leak.secret_values(p) == []
