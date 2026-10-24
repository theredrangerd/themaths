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


@pytest.fixture
def model_says(book_env, monkeypatch):
    """Stub the model; set reply[0] to what it says. Records the prompts it was sent."""
    db.init()
    reply = ["MODEL REPLY"]
    calls = []

    async def stub(model, messages, think, out, show=True, **kw):
        calls.append(messages)
        out.append(reply[0])
        return
        yield

    monkeypatch.setattr(tutor, "_stream", stub)
    return reply, calls


def _model_hint(ref="1.1"):
    """An attempt whose next hint comes from the model (past the book's 2 hints for 1.1)."""
    a = open_(ref)
    run(tutor.hint(a, ""))
    run(tutor.hint(a, ""))
    return a


@pytest.mark.parametrize("reply", [
    "What happens when you put $x$ back in?\n\n**Hint 2:** Use substitution.\nHint 3: Add the numbers.",
    "Hint 1: What happens when you put $x$ back in? Hint 2: Use substitution.",
    "What happens when you put $x$ back in? Hint 2: Use substitution. Hint 3: Add.",
])
def test_model_hint_keeps_only_the_first_hint(model_says, reply):
    model_says[0][0] = reply
    a = _model_hint()
    events = run(tutor.hint(a, "x is 4"))
    assert events[-1]["text"] == "What happens when you put $x$ back in?"


def test_model_hint_without_labels_is_unchanged(model_says):
    text = "This hint is about the expression: what does it give at $x$? A hint: look again."
    model_says[0][0] = text
    a = _model_hint()
    assert run(tutor.hint(a, "x is 4"))[-1]["text"] == text


def test_hint_prompt_shows_only_this_level(model_says):
    _, calls = model_says
    a = _model_hint()
    run(tutor.hint(a, "x is 4"))
    prompt = calls[-1][-1]["content"]
    assert "hint 3 of 3" not in prompt.lower()
    assert "Hint 1, a nudge" not in prompt and "Hint 2, the tool" not in prompt
    assert "The next step: say what to do next" in prompt
    assert "Write only this one hint, with no label and no further hints." in prompt
