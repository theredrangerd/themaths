"""Orchestrates the model, the verifier and the database for each tutoring action.

Every action is an async generator of event dicts, sent to the browser as Server-Sent Events:
  {"type": "status", "text": ...}      progress message
  {"type": "thinking", "chars": n}      the model is reasoning (its thoughts are never shown;
                                         they would give the answer away)
  {"type": "delta", "text": ...}        streamed reply text
  {"type": "done", ...}                 final payload
  {"type": "error", "text": ...}
"""

import asyncio
import json
import random
import re
import time

from . import db, jsonfix, ollama, prompts, sandbox, taxonomy, verify

_TAGS = {
    "statement": {"type": "string"},
    "track": {"type": "string", "enum": ["IB", "AMC"]},
    "topic": {"type": "string"},
    "subtopic": {"type": "string"},
    "difficulty": {"type": "string"},
}
DRAFT_FORMAT = {
    "type": "object",
    "properties": {
        **_TAGS,
        "answer_type": {"type": "string", "enum": list(verify.ANSWER_TYPES)},
        "answer": {"type": "string"},
        "choices": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "string"},
    },
    "required": [*_TAGS, "answer_type", "answer", "verification"],
}
FINISH_FORMAT = {
    "type": "object",
    "properties": {
        "solution": {"type": "string"},
        "hints": {"type": "array", "items": {"type": "string"}},
        "distractors": {"type": "array", "items": {"type": "string"}},
        "consistent": {"type": "boolean"},
        "concern": {"type": "string"},
    },
    "required": ["solution", "hints", "distractors", "consistent", "concern"],
}


# Shared helpers ---------------------------------------------------------

def _messages(user: str, history: list[dict] | None = None) -> list[dict]:
    return [{"role": "system", "content": prompts.system()}, *(history or []), {"role": "user", "content": user}]


async def _stream(model: str, messages: list[dict], think: bool, out: list[str], **kw):
    """Forward a model stream as events; collect the reply text into out[0]."""
    thinking, text = 0, []
    last_sent = 0.0
    async for kind, chunk in ollama.chat(model, messages, think=think, **kw):
        if kind == "thinking":
            thinking += len(chunk)
            if time.monotonic() - last_sent > 0.5:
                last_sent = time.monotonic()
                yield {"type": "thinking", "chars": thinking}
        else:
            text.append(chunk)
            if "format" not in kw:
                yield {"type": "delta", "text": chunk}
    out.append("".join(text))


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise json.JSONDecodeError("no JSON object", text, 0)
    return json.loads(jsonfix.repair(m.group(0)))


def _public(attempt: dict, problem: dict) -> dict:
    problem = dict(problem)
    if not attempt["finished_at"]:
        # The note quotes the computed answer; keep it back until the problem is finished.
        problem["verification_note"] = None
    return {
        "attempt_id": attempt["id"],
        "problem": {
            k: problem[k]
            for k in (
                "id", "statement", "choices", "track", "topic", "subtopic", "difficulty", "answer_type",
                "verification_status", "verification_note", "source", "flagged",
            )
        },
        "attempt": {
            k: attempt[k]
            for k in ("hint_level", "solution_viewed", "outcome", "submissions", "working", "final_answer", "started_at", "finished_at")
        },
        "events": [
            {"kind": e["kind"], "level": e["level"], "content": e["content"], "meta": e["meta"]}
            for e in db.get_events(attempt["id"])
        ],
    }


def attempt_state(attempt_id: int) -> dict | None:
    found = db.get_attempt(attempt_id)
    return _public(*found) if found else None


def _working(text: str) -> str:
    return text.strip() or "(none written yet)"


# Problem creation -------------------------------------------------------
#
# Two stages:
#   1. The model writes the problem, its own answer, and code that computes the answer.
#      The code runs in the sandbox. If the two agree, the problem is "verified". If they
#      disagree, neither is trusted: the model is shown both and asked to reconcile them,
#      and a problem still disputed after the last retry is discarded. (Letting the code
#      win was tried; the code sometimes answers a different question.) If the code
#      cannot run, the problem is served as "unverified".
#   2. The model writes the solution, hints and (for AMC) four distractors, aimed at the
#      settled answer.

def _constraints(track: str | None, topic: str | None, difficulty: str | None) -> str:
    parts = []
    if track:
        parts.append(f"Track: {track}.")
    if topic:
        parts.append(f"Topic: {topic}.")
    if difficulty:
        parts.append(f"Difficulty: {difficulty}.")
    return ("Fixed constraints (these override the request): " + " ".join(parts)) if parts else ""


def _check_draft(data: dict, imported: bool) -> str | None:
    """Return a description of what is wrong with a stage 1 payload, or None."""
    for field in ("statement", "answer", "verification"):
        if not str(data.get(field, "")).strip():
            return f"The field {field} is empty."
    if data.get("answer_type") not in verify.ANSWER_TYPES:
        return "answer_type is not one of the allowed values."
    if data["answer_type"] == "multiple_choice":
        if not imported:
            return "Do not use multiple_choice; give the answer's own type. Options are added later."
        if len(data.get("choices") or []) != 5:
            return "A multiple-choice problem needs its five option values in choices."
        if not re.fullmatch(r"\(?[A-Ea-e]\)?", data["answer"].strip()):
            return "For multiple_choice, answer must be a single letter A to E."
    return None


def _settle(draft: dict) -> dict:
    """Run the draft's code and decide the answer. Returns status, note, answer, error."""
    if draft["answer_type"] == "proof":
        return {"status": "not_applicable", "note": "", "answer": draft["answer"]}
    out = sandbox.run(draft["verification"])
    if not out["ok"]:
        return {"status": "unverified", "answer": draft["answer"], "error": out["error"],
                "note": f"The checking code failed ({out['error']}), so the answer rests on the tutor alone."}
    computed = out["result"]
    agree, why = verify.agrees(computed, draft)
    if agree:
        # Store the computed form: it is already simplified (6120, not a sum of binomials).
        answer = draft["answer"] if draft["answer_type"] == "multiple_choice" else computed
        return {"status": "verified", "answer": answer, "computed": computed,
                "note": f"The tutor's answer and an independent computation both gave {computed}."}
    # Neither side is trustworthy on its own: the code may answer a different question,
    # and the model's arithmetic may be wrong. The caller asks the model to reconcile them.
    return {"status": "disputed", "answer": draft["answer"], "computed": computed, "why": why}


def _distractors_ok(answer: str, distractors: list[str]) -> list[str] | None:
    """Four distinct distractors, none equal to the answer."""
    seen: list = []
    try:
        truth = verify.to_sympy(answer)
    except Exception:
        truth = None
    clean = []
    for d in distractors:
        d = str(d).strip().strip("$")
        try:
            v = verify.to_sympy(d)
        except Exception:
            continue
        if (truth is not None and verify.equal(v, truth)) or any(verify.equal(v, s) for s in seen):
            continue
        seen.append(v)
        clean.append(d)
    return clean[:4] if len(clean) >= 4 else None


def _order_choices(answer: str, distractors: list[str]) -> tuple[list[str], str]:
    """Sort numerically, as AMC options are, and return the answer's letter."""
    options = [answer, *distractors]
    try:
        options.sort(key=lambda x: float(verify.sp.N(verify.to_sympy(x)).as_real_imag()[0]))
    except Exception:
        random.shuffle(options)
    return options, "ABCDE"[options.index(answer)]


async def _json_call(model: str, prompt: str, schema: dict, settings: dict, history: list[dict]):
    """One structured call. Yields events; the final event is {"type": "_json", ...}."""
    out: list[str] = []
    async for event in _stream(model, _messages(prompt, history), settings["think"], out, format=schema, temperature=0.7):
        yield event
    raw = out[0]
    try:
        data = _parse_json(raw)
    except json.JSONDecodeError:
        data = None
    yield {"type": "_json", "data": data, "raw": raw}


async def create_problem(
    *,
    request: str = "",
    track: str | None = None,
    topic: str | None = None,
    difficulty: str | None = None,
    related_attempt: int | None = None,
    statement: str | None = None,
):
    settings = db.get_settings()
    model = settings["reasoning_model"]
    imported = statement is not None
    constraints = _constraints(track, topic, difficulty)

    if imported:
        prompt = prompts.render("import", statement=statement, constraints=constraints, taxonomy=taxonomy.as_prompt_text())
    else:
        related = ""
        if related_attempt and (found := db.get_attempt(related_attempt)):
            p = found[1]
            related = (
                "Write a problem thematically related to the one below, on the same idea, but distinct: "
                "different setting and numbers, and ideally a twist that tests the idea from another angle.\n\n"
                f"Previous problem:\n{p['statement']}"
            )
            track = track or p["track"]
            topic = topic or p["topic"]
        # Name only the recent subtopics: shown full statements, small models copy them.
        recent = db.recent_subtopics(track, topic)
        avoid = ""
        if recent:
            avoid = "Recent problems covered: " + ", ".join(recent) + ". Prefer a different angle or subtopic if the request allows."
        prompt = prompts.render(
            "generate",
            request=request.strip() or "Any problem suited to the constraints below.",
            constraints=constraints,
            taxonomy=taxonomy.as_prompt_text(),
            related=related,
            avoid=avoid,
        )

    tries = int(settings["retries"]) + 1
    history: list[dict] = []
    user_msg = prompt
    best = None  # (draft, settled, finish)
    for i in range(tries):
        yield {"type": "status", "text": "Writing a problem" if i == 0 else f"Trying again ({i + 1} of {tries})"}
        draft = None
        async for event in _json_call(model, user_msg, DRAFT_FORMAT, settings, history):
            if event["type"] == "_json":
                draft, raw = event["data"], event["raw"]
            else:
                yield event
        history += [{"role": "user", "content": user_msg}, {"role": "assistant", "content": raw}]
        if draft is None:
            user_msg = "That was not valid JSON. Return the complete JSON object only."
            continue
        if imported:
            draft["statement"] = statement
        if issue := _check_draft(draft, imported):
            user_msg = f"{issue} Return the complete JSON again with every field."
            continue

        yield {"type": "status", "text": "Checking the answer by computation"}
        settled = await asyncio.to_thread(_settle, draft)
        if settled.get("error") and i + 1 < tries:
            user_msg = f"Your verification code failed: {settled['error']}. Fix it. Return the complete JSON again with every field."
            continue
        if settled["status"] == "disputed":
            user_msg = prompts.render("reconcile", answer=draft["answer"], computed=settled["computed"], code=draft["verification"])
            continue

        yield {"type": "status", "text": "Writing the solution and hints"}
        mc_new = not imported and draft.get("track") == "AMC" and draft["answer_type"] != "proof"
        shown_answer = settled["answer"]
        if draft["answer_type"] == "multiple_choice":
            letter = settled["answer"]
            value = draft["choices"]["ABCDE".index(letter.strip("()").upper())]
            shown_answer = f"({letter}) {value}"
        finish_prompt = prompts.render(
            "finish",
            statement=draft["statement"],
            answer=shown_answer,
            choices_task="This is a five-option multiple-choice contest problem." if mc_new else "",
            distractor_rule=(
                "exactly four wrong answers, each a value a student would plausibly reach by a specific common mistake "
                "(e.g. an off-by-one, overcounting, a sign error). Plain LaTeX without $ signs, all different from the answer and each other."
                if mc_new else "an empty list."
            ),
        )
        finish, distractors = None, None
        for _ in range(2):
            async for event in _json_call(model, finish_prompt, FINISH_FORMAT, settings, []):
                if event["type"] == "_json":
                    finish = event["data"]
                else:
                    yield event
            if not finish or not str(finish.get("solution", "")).strip():
                finish = None
                continue
            if mc_new:
                distractors = await asyncio.to_thread(_distractors_ok, settled["answer"], finish.get("distractors") or [])
                if distractors is None:
                    finish = None
                    continue
            break
        if finish is None:
            user_msg = "Write a different problem that meets the same request. Return the complete JSON with every field."
            continue

        best = (draft, settled, finish, distractors)
        if finish.get("consistent", True):
            break
        concern = str(finish.get("concern", "")).strip()
        if i + 1 == tries:
            settled["status"] = "unverified"
            settled["note"] = f"{settled.get('note', '')} When writing the solution, the tutor doubted this answer: {concern}".strip()
            break
        user_msg = (
            f"When writing a solution for the answer {settled['answer']}, you concluded: {concern} "
            "Write a different, sound problem instead. Return the complete JSON with every field."
        )

    if best is None:
        yield {
            "type": "error",
            "text": "The model couldn't produce a problem whose answer checks out, so none is shown. "
            "Try again, or ask for something a little different. A larger tutor model fails less often.",
        }
        return

    draft, settled, finish, distractors = best
    answer_type, answer, choices = draft["answer_type"], settled["answer"], draft.get("choices") or []
    if distractors is not None:
        choices, answer = await asyncio.to_thread(_order_choices, settled["answer"], distractors)
        answer_type = "multiple_choice"
    elif answer_type != "multiple_choice":
        choices = []

    tags = taxonomy.normalise_tags(draft["track"], draft["topic"], draft["subtopic"], draft["difficulty"])
    problem_id = db.insert_problem(
        {
            **tags,
            "source": "screenshot" if imported else "generated",
            "request": request,
            "statement": draft["statement"],
            "choices": [str(c) for c in choices],
            "answer_type": answer_type,
            "answer": str(answer).strip(),
            "verification": draft.get("verification"),
            "verification_status": settled["status"],
            "verification_note": settled.get("note", ""),
            "hints": [str(h) for h in (finish.get("hints") or [])][:3],
            "solution": finish["solution"],
            "model": model,
        }
    )
    attempt_id = db.start_attempt(problem_id)
    yield {"type": "done", "state": attempt_state(attempt_id)}


async def transcribe(image_b64: str):
    settings = db.get_settings()
    out: list[str] = []
    messages = [{"role": "user", "content": prompts.render("transcribe"), "images": [image_b64]}]
    yield {"type": "status", "text": "Reading the screenshot"}
    async for event in _stream(settings["vision_model"], messages, False, out, temperature=0.1):
        yield event
    yield {"type": "done", "statement": out[0].strip()}


# Actions on an attempt --------------------------------------------------

def _load(attempt_id: int) -> tuple[dict, dict]:
    found = db.get_attempt(attempt_id)
    if not found:
        raise LookupError("No such attempt.")
    return found


def _finish(attempt: dict, outcome: str) -> dict:
    fields = {"outcome": outcome}
    if not attempt["finished_at"]:
        fields["finished_at"] = time.time()
    return fields


def check_answer(attempt_id: int, answer: str, working: str) -> dict:
    attempt, problem = _load(attempt_id)
    verdict = verify.check(answer, problem)
    fields = {"final_answer": answer, "working": working}
    if verdict.result in ("correct", "incorrect"):
        fields["submissions"] = attempt["submissions"] + 1
    if verdict.result == "correct" and attempt["outcome"] in (None, "incorrect"):
        fields.update(_finish(attempt, "correct"))
    elif verdict.result == "incorrect" and attempt["outcome"] is None:
        fields["outcome"] = "incorrect"
    db.update_attempt(attempt_id, **fields)
    if verdict.result in ("correct", "incorrect"):
        db.add_event(attempt_id, "check", answer, meta={"result": verdict.result, "note": verdict.note})
    return {"result": verdict.result, "note": verdict.note, "state": attempt_state(attempt_id)}


async def _respond(attempt_id: int, kind: str, prompt: str, level: int | None = None, meta: dict | None = None):
    settings = db.get_settings()
    out: list[str] = []
    async for event in _stream(settings["reasoning_model"], _messages(prompt), settings["think"], out):
        yield event
    text = out[0].strip()
    db.add_event(attempt_id, kind, text, level=level, meta=meta)
    yield {"type": "done", "text": text, "state": attempt_state(attempt_id)}


async def hint(attempt_id: int, working: str):
    attempt, problem = _load(attempt_id)
    level = min(attempt["hint_level"] + 1, 4)
    db.update_attempt(attempt_id, working=working)
    if level == 4:
        async for event in solution(attempt_id):
            yield event
        return
    db.update_attempt(attempt_id, hint_level=level)
    prepared = problem["hints"][level - 1] if len(problem["hints"]) >= level else ""
    if not working.strip() and prepared:
        # Nothing to tailor to: the prepared hint is instant.
        db.add_event(attempt_id, "hint", prepared, level=level)
        yield {"type": "done", "text": prepared, "state": attempt_state(attempt_id)}
        return
    previous = [e["content"] for e in db.get_events(attempt_id) if e["kind"] == "hint"]
    prompt = prompts.render(
        "hint",
        level=str(level),
        statement=problem["statement"],
        solution=problem["solution"],
        answer=problem["answer"],
        previous="\n".join(f"{i + 1}. {h}" for i, h in enumerate(previous)) or "(none)",
        prepared=prepared or "(none)",
        working=_working(working),
    )
    async for event in _respond(attempt_id, "hint", prompt, level=level):
        yield event


async def solution(attempt_id: int):
    attempt, problem = _load(attempt_id)
    fields = {"hint_level": 4, "solution_viewed": 1}
    if attempt["outcome"] in (None, "incorrect"):
        fields.update(_finish(attempt, "gave_up"))
    db.update_attempt(attempt_id, **fields)
    db.add_event(attempt_id, "solution", problem["solution"], level=4)
    yield {"type": "done", "text": problem["solution"], "state": attempt_state(attempt_id)}


async def analyse(attempt_id: int, working: str, answer: str):
    attempt, problem = _load(attempt_id)
    db.update_attempt(attempt_id, working=working, final_answer=answer)
    if problem["answer_type"] == "proof":
        prompt = prompts.render("proof_review", statement=problem["statement"], solution=problem["solution"], working=_working(working + "\n" + answer))
        settings = db.get_settings()
        out: list[str] = []
        async for event in _stream(settings["reasoning_model"], _messages(prompt), settings["think"], out):
            yield event
        text = out[0].strip()
        complete = bool(re.match(r"\W*verdict:\s*complete", text, re.I))
        db.add_event(attempt_id, "analysis", text, meta={"proof": True, "complete": complete})
        if complete and attempt["outcome"] in (None, "incorrect"):
            db.update_attempt(attempt_id, submissions=attempt["submissions"] + 1, **_finish(attempt, "correct"))
        else:
            db.update_attempt(attempt_id, submissions=attempt["submissions"] + 1, outcome=attempt["outcome"] or "incorrect")
        yield {"type": "done", "text": text, "state": attempt_state(attempt_id)}
        return
    prompt = prompts.render(
        "analysis",
        statement=problem["statement"],
        solution=problem["solution"],
        answer=problem["answer"],
        final_answer=answer or "(none)",
        working=_working(working),
    )
    async for event in _respond(attempt_id, "analysis", prompt):
        yield event


async def explain(attempt_id: int, working: str, question: str):
    attempt, problem = _load(attempt_id)
    db.update_attempt(attempt_id, working=working)
    prompt = prompts.render(
        "explain",
        statement=problem["statement"],
        solution=problem["solution"],
        working=_working(working),
        question=question.strip() or "Why does the method for this problem work? What is the underlying idea?",
    )
    async for event in _respond(attempt_id, "explain", prompt, meta={"question": question}):
        yield event


async def flag(attempt_id: int, reason: str):
    attempt, problem = _load(attempt_id)
    choices = "\n".join(f"({l}) {c}" for l, c in zip("ABCDE", problem["choices"])) or "(not multiple choice)"
    prompt = prompts.render(
        "flag",
        statement=problem["statement"],
        choices=choices,
        answer=problem["answer"],
        solution=problem["solution"],
        reason=reason.strip() or "(no reason given)",
    )
    db.flag_problem(problem["id"])
    db.update_attempt(attempt_id, **_finish(attempt, "flagged"))
    settings = db.get_settings()
    out: list[str] = []
    async for event in _stream(settings["reasoning_model"], _messages(prompt), settings["think"], out):
        yield event
    text = out[0].strip()
    m = re.match(r"\W*verdict:\s*(flawed|sound|unclear)", text, re.I)
    db.add_event(attempt_id, "flag", text, meta={"reason": reason, "verdict": m.group(1).lower() if m else "unclear"})
    yield {"type": "done", "text": text, "state": attempt_state(attempt_id)}
