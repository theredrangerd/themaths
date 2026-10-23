"""Orchestrates the model, the verifier and the database for each tutoring action.

Every action is an async generator of event dicts, sent to the browser as Server-Sent Events:
  {"type": "status", "text": ...}      progress message
  {"type": "thinking", "chars": n}      the model is reasoning (its thoughts are never shown;
                                         they would give the answer away)
  {"type": "delta", "text": ...}        streamed reply text
  {"type": "writing", "chars": n}       a hidden reply (it contains the answer) is n characters along
  {"type": "done", ...}                 final payload
  {"type": "error", "text": ...}
"""

import asyncio
import logging
import random
import re
import time

from . import db, ollama, prompts, sandbox, sections, taxonomy, verify
from .config import DATA_DIR

log = logging.getLogger("themaths.tutor")

# Shared helpers ---------------------------------------------------------

def _messages(user: str, history: list[dict] | None = None) -> list[dict]:
    return [{"role": "system", "content": prompts.system()}, *(history or []), {"role": "user", "content": user}]


async def _stream(model: str, messages: list[dict], think: bool, out: list[str], show: bool = True, **kw):
    """Forward a model stream as events; collect the reply text into out[0].

    With show=False (replies that contain the answer) only progress is reported."""
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
            if show:
                yield {"type": "delta", "text": chunk}
            elif time.monotonic() - last_sent > 0.5:
                last_sent = time.monotonic()
                yield {"type": "writing", "chars": sum(map(len, text))}
    out.append("".join(text))


def _save_debug(kind: str, text: str) -> str:
    """Keep a reply we couldn't use, for diagnosing prompt or parsing problems."""
    folder = DATA_DIR / "debug"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.txt"
    path.write_text(text)
    return str(path)


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
#   2. The model solves the problem again without being told the answer, and writes the
#      hints and (for AMC) four distractors. Its boxed answer must match the settled one;
#      otherwise the problem goes back for another try, and is discarded after the last.
#      (Telling it the answer was tried; it would notice a contradiction and then fudge
#      its working to reach the given answer anyway.)

AGAIN = "Reply again in full, in the same format, with every section."


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
            return f"The {field} section is empty."
    if data.get("answer_type") not in verify.ANSWER_TYPES:
        return "The answer type is not one of the allowed values."
    if not imported and data.get("track") == "AMC":
        if data["answer_type"] == "proof":
            return "AMC problems have a single numerical answer, not a proof."
        try:
            if verify.to_sympy(data["answer"]).free_symbols:
                return "AMC answers are a single number (exact values like 2\\sqrt{3} are fine), not a formula in variables. Fix concrete values in the problem."
        except Exception:
            pass
    if data["answer_type"] == "multiple_choice":
        if not imported:
            return "Do not use multiple_choice; give the answer's own type. Options are added later."
        if len(data.get("choices") or []) != 5:
            return "A multiple-choice problem needs its five option values in the Choices section."
        if not re.fullmatch(r"\(?[A-Ea-e]\)?", data["answer"].strip()):
            return "For multiple_choice, the answer must be a single letter A to E."
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


def _solution_agrees(boxed: str, draft: dict, settled: dict) -> bool:
    """Does the blind solution's boxed answer match the settled answer?"""
    value = re.sub(r"\\text(?:bf)?\{([^{}]*)\}", r"\1", boxed or "").strip()
    if draft["answer_type"] == "multiple_choice" and (m := re.match(r"^\(?([A-Ea-e])\)?(?:\s|$)", value)):
        value = m.group(1)
    target = {"answer": settled["answer"], "answer_type": draft["answer_type"], "choices": draft.get("choices") or []}
    ok, _ = verify.agrees(value, target)
    return ok


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


async def _sections_call(model: str, prompt: str, parse, settings: dict, history: list[dict]):
    """One hidden call parsed into sections. The final event is {"type": "_parsed", ...}."""
    out: list[str] = []
    async for event in _stream(model, _messages(prompt, history), settings["think"], out, show=False, temperature=0.7):
        yield event
    raw = out[0]
    fields, missing = parse(raw)
    yield {"type": "_parsed", "fields": fields, "missing": missing, "raw": raw}


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
        async for event in _sections_call(model, user_msg, sections.draft, settings, history):
            if event["type"] == "_parsed":
                draft, missing, raw = event["fields"], event["missing"], event["raw"]
            else:
                yield event
        history += [{"role": "user", "content": user_msg}, {"role": "assistant", "content": raw}]
        if imported:
            draft["statement"] = statement
            missing = [m for m in missing if m != "statement"]
        if missing:
            log.warning("draft %d: missing sections %s, saved to %s", i + 1, missing, _save_debug("draft", raw))
            user_msg = f"Your reply was missing these sections: {', '.join(missing)}. {AGAIN}"
            continue
        if issue := _check_draft(draft, imported):
            log.warning("draft %d: %s", i + 1, issue)
            user_msg = f"{issue} {AGAIN}"
            continue

        yield {"type": "status", "text": "Checking the answer by computation"}
        settled = await asyncio.to_thread(_settle, draft)
        log.warning("draft %d: %s; answer=%r computed=%r %s", i + 1, settled["status"], draft["answer"],
                    settled.get("computed"), settled.get("error") or settled.get("why") or "")
        if settled.get("error"):
            log.warning("draft %d: failing code saved to %s", i + 1, _save_debug("code", draft["verification"]))
        if settled.get("error") and i + 1 < tries:
            fix = (
                "Rewrite it so the code derives the answer itself: for example, loop over the candidates and test the "
                "problem's conditions, or set up the equations in sympy and solve them."
                if "literal" in settled["error"] else "Fix it."
            )
            user_msg = f"Your verification code failed: {settled['error']}. {fix} {AGAIN}"
            continue
        if settled["status"] == "disputed":
            user_msg = prompts.render("reconcile", answer=draft["answer"], computed=settled["computed"], code=draft["verification"])
            continue

        yield {"type": "status", "text": "Solving it independently and writing hints"}
        mc_new = not imported and draft.get("track") == "AMC" and draft["answer_type"] != "proof"
        if mc_new:
            choices_task = "It will be shown as a five-option multiple-choice contest problem, but solve it as free response."
            boxed_rule = " Box the value itself."
        elif draft["answer_type"] == "multiple_choice":
            choices_task = "It is multiple choice; the options are part of the problem above."
            boxed_rule = " Box the letter of the correct option, e.g. \\boxed{C}."
        else:
            choices_task, boxed_rule = "", ""
        finish_prompt = prompts.render(
            "finish",
            statement=draft["statement"],
            choices_task=choices_task,
            boxed_rule=boxed_rule,
            distractor_rule=(
                "Exactly four wrong answers, one per line as \"- value\", each a value a student would plausibly reach by a "
                "specific common mistake (an off-by-one, overcounting, a sign error). All different from the correct answer and each other."
                if mc_new else "Leave empty."
            ),
        )
        finish, distractors = None, None
        for _ in range(2):
            async for event in _sections_call(model, finish_prompt, sections.finish, settings, []):
                if event["type"] == "_parsed":
                    finish, missing, raw = event["fields"], event["missing"], event["raw"]
                else:
                    yield event
            if missing:
                log.warning("draft %d: finish missing %s, saved to %s", i + 1, missing, _save_debug("finish", raw))
                finish = None
                continue
            if mc_new:
                distractors = await asyncio.to_thread(_distractors_ok, settled["answer"], finish["distractors"])
                if distractors is None:
                    log.warning("draft %d: unusable distractors %s", i + 1, finish["distractors"])
                    finish = None
                    continue
            break
        if finish is None:
            log.warning("draft %d: finish stage failed twice", i + 1)
            user_msg = f"Write a different problem that meets the same request. {AGAIN}"
            continue

        # The solution was written without seeing the answer: a third, independent route.
        if draft["answer_type"] != "proof":
            agree = await asyncio.to_thread(_solution_agrees, finish["boxed"], draft, settled)
            log.warning("draft %d: blind solution boxed %r, %s", i + 1, finish["boxed"], "agrees" if agree else "DISAGREES")
            if not agree:
                user_msg = prompts.render("mismatch", answer=draft["answer"], other=finish["boxed"], solution=finish["solution"])
                continue
            if settled["status"] == "verified":
                settled["note"] = (
                    f"The tutor's answer, an independent computation and a separately worked solution all gave {settled['computed']}."
                )
        best = (draft, settled, finish, distractors)
        break

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
            "hints": finish["hints"],
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
