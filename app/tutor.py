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

from . import books, db, leak, ollama, prompts, sandbox, sections, taxonomy, verify
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
    if problem["source"] == "generated" and not attempt["finished_at"]:
        # A generated problem's note quotes the computed answer; keep it back until the
        # problem is finished. Notes on imported problems never quote the answer, and
        # their warnings matter before the first hint.
        problem["verification_note"] = None
    return {
        "attempt_id": attempt["id"],
        "problem": {
            k: problem[k]
            for k in (
                "id", "statement", "choices", "track", "topic", "subtopic", "difficulty", "answer_type",
                "verification_status", "verification_note", "source", "flagged", "book_ref", "book_hints",
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


def _finish_prompt(draft: dict, mc_new: bool) -> str:
    if mc_new:
        choices_task = "It will be shown as a five-option multiple-choice contest problem, but solve it as free response."
    elif draft["answer_type"] == "multiple_choice":
        choices_task = "It is multiple choice; the options are part of the problem above."
    else:
        choices_task = ""
    return prompts.render(
        "finish",
        statement=draft["statement"],
        choices_task=choices_task,
        boxed_rule=_boxed_rule(draft, mc_new),
        distractor_rule=(
            "Exactly four wrong answers, one per line as \"- value\", each a value a student would plausibly reach by a "
            "specific common mistake (an off-by-one, overcounting, a sign error). All different from the correct answer and each other."
            if mc_new else "Leave empty."
        ),
    )


def _boxed_rule(draft: dict, mc_new: bool) -> str:
    if mc_new:
        return " Box the value itself."
    if draft["answer_type"] == "multiple_choice":
        return " Box the letter of the correct option, e.g. \\boxed{C}."
    return ""


async def _solve_blind(model: str, settings: dict, prompt: str, label: str, answer_for_distractors: str | None = None):
    """Up to two tries at a stage 2 reply. The final event is {"type": "_solved", "finish", "distractors"}."""
    finish, distractors = None, None
    for _ in range(2):
        async for event in _sections_call(model, prompt, sections.finish, settings, []):
            if event["type"] == "_parsed":
                finish, missing, raw = event["fields"], event["missing"], event["raw"]
            else:
                yield event
        if missing:
            log.warning("%s: solution missing %s, saved to %s", label, missing, _save_debug("finish", raw))
            finish = None
            continue
        if answer_for_distractors is not None:
            distractors = await asyncio.to_thread(_distractors_ok, answer_for_distractors, finish["distractors"])
            if distractors is None:
                log.warning("%s: unusable distractors %s", label, finish["distractors"])
                finish = None
                continue
        break
    yield {"type": "_solved", "finish": finish, "distractors": distractors}


def _as_letter(value: str, choices: list[str]) -> str | None:
    """A multiple-choice answer given as a letter or as one option's value, as a letter."""
    v = re.sub(r"\\text(?:bf)?\{([^{}]*)\}", r"\1", value or "").strip()
    if m := re.match(r"^\(?([A-Ea-e])\)?(?:\s|$)", v):
        return m.group(1).upper()
    for letter, c in zip("ABCDE", choices):
        try:
            if verify.equal(verify.to_sympy(v), verify.to_sympy(c)):
                return letter
        except Exception:
            continue
    return None


def _same(a: str | None, b: str | None, draft: dict) -> bool:
    """Do two answers to this problem agree? Never raises."""
    if not a or not b:
        return False
    try:
        if draft["answer_type"] == "multiple_choice":
            la, lb = _as_letter(a, draft["choices"]), _as_letter(b, draft["choices"])
            return la is not None and la == lb
        ok, _ = verify.agrees(a, {"answer": b, "answer_type": draft["answer_type"], "choices": []})
        return ok
    except Exception:
        return False


async def create_problem(
    *,
    request: str = "",
    track: str | None = None,
    topic: str | None = None,
    difficulty: str | None = None,
    related_attempt: int | None = None,
):
    settings = db.get_settings()
    model = settings["reasoning_model"]
    constraints = _constraints(track, topic, difficulty)

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
        if missing:
            log.warning("draft %d: missing sections %s, saved to %s", i + 1, missing, _save_debug("draft", raw))
            user_msg = f"Your reply was missing these sections: {', '.join(missing)}. {AGAIN}"
            continue
        if issue := _check_draft(draft, imported=False):
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
        mc_new = draft.get("track") == "AMC" and draft["answer_type"] != "proof"
        async for event in _solve_blind(model, settings, _finish_prompt(draft, mc_new), f"draft {i + 1}",
                                        settled["answer"] if mc_new else None):
            if event["type"] == "_solved":
                finish, distractors = event["finish"], event["distractors"]
            else:
                yield event
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
            "source": "generated",
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


async def import_problem(*, statement: str, book_answer: str = "", working: str = "", track: str | None = None):
    """Prepare a problem the student brought (usually a screenshot) for hints and marking.

    Unlike generated problems, an imported one is never discarded: the student needs help
    with this exact problem. The book's answer, when given, is the truth; otherwise the
    tutor's answer, its checking code and a blind solution vote, and disagreement is shown.
    """
    settings = db.get_settings()
    model = settings["reasoning_model"]
    book = book_answer.strip()
    prompt = prompts.render("import", statement=statement, constraints=_constraints(track, None, None),
                            taxonomy=taxonomy.as_prompt_text())

    # Stage 1: tags, the tutor's own answer, and checking code.
    draft, history, user_msg = None, [], prompt
    for i in range(2):
        yield {"type": "status", "text": "Reading the problem" if i == 0 else "Reading the problem again"}
        async for event in _sections_call(model, user_msg, sections.draft, settings, history):
            if event["type"] == "_parsed":
                fields, missing, raw = event["fields"], event["missing"], event["raw"]
            else:
                yield event
        history += [{"role": "user", "content": user_msg}, {"role": "assistant", "content": raw}]
        fields["statement"] = statement
        missing = [m for m in missing if m != "statement"]
        issue = f"Your reply was missing these sections: {', '.join(missing)}." if missing else _check_draft(fields, imported=True)
        if not issue:
            draft = fields
            break
        log.warning("import draft %d: %s, saved to %s", i + 1, issue, _save_debug("import", raw))
        user_msg = f"{issue} {AGAIN}"
    if draft is None:
        yield {"type": "error", "text": "The tutor couldn't make sense of this problem. Check the transcription for errors and try again."}
        return

    truth = None
    if book and draft["answer_type"] == "multiple_choice":
        truth = _as_letter(book, draft["choices"])
        if truth is None:
            # The options weren't transcribed in a usable way; treat it as free response.
            draft["answer_type"], draft["choices"] = "exact_expression", []
    if book and truth is None and draft["answer_type"] != "proof":
        truth = book

    settled = {"status": "not_applicable", "answer": draft["answer"]}
    if draft["answer_type"] != "proof":
        yield {"type": "status", "text": "Checking the tutor's answer by computation"}
        settled = await asyncio.to_thread(_settle, draft)
        log.warning("import: %s; answer=%r computed=%r book=%r", settled["status"], draft["answer"], settled.get("computed"), truth)

    # Stage 2: the tutor solves it without being told any answer.
    yield {"type": "status", "text": "Solving it and writing hints"}
    finish = None
    async for event in _solve_blind(model, settings, _finish_prompt(draft, False), "import"):
        if event["type"] == "_solved":
            finish = event["finish"]
        else:
            yield event
    if finish is None:
        yield {"type": "error", "text": "The tutor couldn't write a solution to this problem. Try again; if it keeps failing, a larger tutor model may help."}
        return
    boxed = finish["boxed"]
    log.warning("import: blind solution boxed %r", boxed)

    if draft["answer_type"] == "proof":
        status, answer, note = "not_applicable", draft["answer"], ""
    elif truth is not None:
        answer, status = truth, "book"
        if await asyncio.to_thread(_same, boxed, truth, draft):
            status, note = "verified", "The tutor's worked solution reaches the answer from your book."
        else:
            yield {"type": "status", "text": "The tutor disagreed with your book; finding its mistake"}
            fixed = None
            correct_prompt = prompts.render("correct", boxed=boxed, book=book, statement=statement,
                                            solution=finish["solution"], boxed_rule=_boxed_rule(draft, False))
            async for event in _solve_blind(model, settings, correct_prompt, "import correction"):
                if event["type"] == "_solved":
                    fixed = event["finish"]
                else:
                    yield event
            if fixed and await asyncio.to_thread(_same, fixed["boxed"], truth, draft):
                finish = fixed
                note = "The tutor's first solution disagreed with your book's answer; it found its mistake and corrected it."
            else:
                note = ("The tutor couldn't reach your book's answer, so its hints and solution may be unreliable. "
                        "Marking uses your book's answer.")
            log.warning("import: correction %s", "succeeded" if fixed and finish is fixed else "failed")
    else:
        # No book answer: majority vote between the tutor, its code and its blind solution.
        votes = {"the tutor's answer": draft["answer"], "a computation": settled.get("computed"), "a worked solution": boxed}
        names = list(votes)
        agreeing = [(a, b) for k, a in enumerate(names) for b in names[k + 1:]
                    if await asyncio.to_thread(_same, votes[a], votes[b], draft)]
        if settled["status"] == "verified" and _same(boxed, settled["answer"], draft):
            status, answer = "verified", settled["answer"]
            note = "The tutor's answer, an independent computation and a worked solution all agree."
        elif agreeing:
            a, b = agreeing[0]
            answer, status = votes[a], "unverified"
            if draft["answer_type"] == "multiple_choice":
                answer = _as_letter(answer, draft["choices"]) or answer
            note = (f"{a[0].upper()}{a[1:]} and {b} agree, but the third check didn't, so treat the marking with a little caution. "
                    "Entering your book's answer makes it reliable.")
        else:
            answer, status = (draft["answer"] if draft["answer"] else boxed), "unverified"
            note = ("The tutor's methods all disagreed, so its answer, hints and marking may be wrong. "
                    "Entering your book's answer makes marking reliable.")

    tags = taxonomy.normalise_tags(draft["track"], draft["topic"], draft["subtopic"], draft["difficulty"])
    problem_id = db.insert_problem(
        {
            **tags,
            "source": "screenshot",
            "request": "",
            "statement": statement,
            "choices": draft["choices"] if draft["answer_type"] == "multiple_choice" else [],
            "answer_type": draft["answer_type"],
            "answer": str(answer).strip(),
            "verification": draft.get("verification"),
            "verification_status": status,
            "verification_note": note,
            "hints": finish["hints"],
            "solution": finish["solution"],
            "model": model,
        }
    )
    attempt_id = db.start_attempt(problem_id)
    if working.strip():
        db.update_attempt(attempt_id, working=working)
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


_IMG = re.compile(r"(/api/books/[a-z0-9-]+/[a-z0-9-]+/img/[\w-]+\.(?:png|jpg|svg))")


def _ensure_book_problem(book: str, chapter: str, ref: str) -> int:
    ch = books.load_chapter(book, chapter)
    return db.upsert_book_problem(books.problem_row(ch, books.find_problem(ch, ref)))


def _latest_or_new(problem_id: int) -> int:
    return db.latest_attempt(problem_id) or db.start_attempt(problem_id)


def open_book_problem(book: str, chapter: str, ref: str) -> dict:
    """Upsert a book problem from its YAML, resume its latest attempt (or start one), return the state."""
    return attempt_state(_latest_or_new(_ensure_book_problem(book, chapter, ref)))


def retry(attempt_id: int) -> dict:
    _, problem = _load(attempt_id)
    return attempt_state(db.start_attempt(problem["id"]))


def self_mark(attempt_id: int, got_it: bool) -> dict:
    attempt, _ = _load(attempt_id)
    if not attempt["solution_viewed"]:
        raise ValueError("Reveal the solution before marking yourself.")
    db.update_attempt(attempt_id, **_finish(attempt, "correct" if got_it else "gave_up"))
    return attempt_state(attempt_id)


def _mark_siblings_seen(book_ref: str) -> None:
    """A solution shared with other parts shows them too: count those as seen, and give up if open."""
    for sib in books.siblings(book_ref):
        book, chapter, ref = sib.split("/", 2)
        attempt_id = _latest_or_new(_ensure_book_problem(book, chapter, ref))
        attempt, _ = _load(attempt_id)
        fields = {"solution_viewed": 1}
        if attempt["outcome"] in (None, "incorrect"):
            fields.update(_finish(attempt, "gave_up"))
        db.update_attempt(attempt_id, **fields)


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


# A "Hint N:" label, at the start of the reply, a line or a sentence.
_HINT_LABEL = re.compile(r"(?:^|(?<=[.?!)]))\s*[*_#>\s-]*hint\s*[1-9]\b\W*?[:.)]", re.I | re.M)


def _one_hint(text: str) -> str:
    """Drop a leading "Hint N:" label, and cut the reply at any later hint the model added.

    Asked for one hint, the model sometimes writes the next ones too ("Hint 2: ...",
    "Hint 3: ..."), which would show the student later rungs early.
    """
    if m := _HINT_LABEL.match(text):
        text = text[m.end():].lstrip("*_ \t\n")
    if (m := _HINT_LABEL.search(text)) and m.start() > 0:
        text = text[:m.start()]
    return text.strip()


async def _respond(attempt_id: int, kind: str, prompt: str, *, problem: dict, attempt: dict,
                   think: bool | None = None, level: int | None = None, meta: dict | None = None,
                   working: str | None = None):
    """Reply to the student. While the problem is unsolved, the reply is written hidden and
    checked for the answer before it is shown: asked not to, small models state it anyway."""
    settings = db.get_settings()
    model = settings["reasoning_model"]
    think = settings["think"] if think is None else think
    guard = not attempt["finished_at"]
    out: list[str] = []
    async for event in _stream(model, _messages(prompt), think, out, show=not guard):
        yield event
    text = out[0].strip()
    if kind == "hint":
        text = _one_hint(text)
    if guard and (found := await asyncio.to_thread(leak.find, text, problem)):
        log.warning("%s gave the answer away (%s); rewording", kind, found)
        yield {"type": "status", "text": "Rewording so it doesn't give the answer away"}
        out = []
        rewrite = prompts.render("unleak", text=text, leaks=", ".join(dict.fromkeys(found)))
        async for event in _stream(model, _messages(rewrite), False, out, show=False):
            yield event
        rewritten = out[0].strip() or text
        if still := await asyncio.to_thread(leak.find, rewritten, problem):
            log.warning("%s still gave the answer away (%s); hiding it", kind, still)
            rewritten = leak.redact(rewritten, still)
        text = rewritten
    if guard and working is not None:
        # Hints: no values from steps the student hasn't done yet.
        if early := leak.ahead(text, problem["statement"], working):
            log.warning("%s jumped ahead of the student (%s); rewording", kind, early)
            yield {"type": "status", "text": "Making sure the hint doesn't do the step for you"}
            out = []
            rehint = prompts.render("rehint", values=", ".join(early), statement=problem["statement"],
                                    working=_working(working), text=text)
            async for event in _stream(model, _messages(rehint), False, out, show=False):
                yield event
            rewritten = out[0].strip()
            if rewritten and not leak.find(rewritten, problem):
                text = rewritten
    db.add_event(attempt_id, kind, text, level=level, meta=meta)
    yield {"type": "done", "text": text, "state": attempt_state(attempt_id)}


# Only the current level's rule goes into the prompt: shown all three, the model wrote all three.
_HINT_RULES = {
    1: "A nudge: one or two sentences, as a question or an observation that redirects attention "
       '("What do you notice about ...?"). No formulas, no method names.',
    2: "The tool: name the technique, theorem or property that unlocks the next part, and say in one "
       "sentence why it fits. No algebra.",
    3: "The next step: say what to do next as one instruction. You may write at most one short "
       "expression, and only one built from things already in the problem or the student's working. "
       "Do not carry the step out or show its result.",
}


async def hint(attempt_id: int, working: str):
    attempt, problem = _load(attempt_id)
    level = min(attempt["hint_level"] + 1, 4)
    db.update_attempt(attempt_id, working=working)
    if level == 4:
        async for event in solution(attempt_id):
            yield event
        return
    db.update_attempt(attempt_id, hint_level=level)
    if level <= (problem.get("book_hints") or 0):
        # The book's own hint: served verbatim, with no model call and no leak checks.
        text = problem["hints"][level - 1]
        db.add_event(attempt_id, "hint", text, level=level, meta={"book": True})
        yield {"type": "done", "text": text, "state": attempt_state(attempt_id)}
        return
    prepared = problem["hints"][level - 1] if len(problem["hints"]) >= level else ""
    if (not working.strip() and prepared and not leak.find(prepared, problem)
            and not leak.ahead(prepared, problem["statement"], "")):
        # Nothing to tailor to: the prepared hint is instant.
        db.add_event(attempt_id, "hint", prepared, level=level)
        yield {"type": "done", "text": prepared, "state": attempt_state(attempt_id)}
        return
    previous = [e["content"] for e in db.get_events(attempt_id) if e["kind"] == "hint"]
    prompt = prompts.render(
        "hint",
        level_rule=_HINT_RULES[level],
        statement=problem["statement"],
        solution=problem["solution"],
        answer=problem["answer"],
        previous="\n".join(f"{i + 1}. {h}" for i, h in enumerate(previous)) or "(none)",
        prepared=prepared or "(none)",
        working=_working(working),
    )
    async for event in _respond(attempt_id, "hint", prompt, problem=problem, attempt=attempt, level=level, working=working):
        yield event


async def solution(attempt_id: int):
    attempt, problem = _load(attempt_id)
    fields = {"hint_level": 4, "solution_viewed": 1}
    text = problem["solution"]
    if problem["source"] == "book":
        text = _IMG.sub(rf"\1?attempt={attempt_id}", text)
        if problem["answer_type"] != "self":
            # A self-marked problem stays open until the student says how they did.
            if attempt["outcome"] in (None, "incorrect"):
                fields.update(_finish(attempt, "gave_up"))
        _mark_siblings_seen(problem["book_ref"])
    elif attempt["outcome"] in (None, "incorrect"):
        fields.update(_finish(attempt, "gave_up"))
    db.update_attempt(attempt_id, **fields)
    db.add_event(attempt_id, "solution", text, level=4)
    yield {"type": "done", "text": text, "state": attempt_state(attempt_id)}


async def analyse(attempt_id: int, working: str, answer: str):
    attempt, problem = _load(attempt_id)
    db.update_attempt(attempt_id, working=working, final_answer=answer)
    if problem["answer_type"] == "proof":
        prompt = prompts.render("proof_review", statement=problem["statement"], solution=problem["solution"], working=_working(working + "\n" + answer))
        settings = db.get_settings()
        out: list[str] = []
        async for event in _stream(settings["reasoning_model"], _messages(prompt), True, out):
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
    # Thinking is always on here: without it, qwen3:14b blamed a correct step instead of the real mistake.
    async for event in _respond(attempt_id, "analysis", prompt, problem=problem, attempt=attempt, think=True):
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
    async for event in _respond(attempt_id, "explain", prompt, problem=problem, attempt=attempt, meta={"question": question}):
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
