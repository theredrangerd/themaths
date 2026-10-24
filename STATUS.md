# Status: where v1 stands (updated 2026-10-23)

Working notes for picking the build back up. SPEC.md is the original target; this file records what's built, what testing showed, and what's left.

## Main use case

The student's main use is **help on problems from their AoPS books**: screenshot a problem, optionally enter the book's final answer and their working so far, then use hints, error analysis and "Why?". AI-generated problems are secondary, and a slightly easy difficulty is acceptable. The student wants a hint, never the book's solution, so the app never asks them to read or paste one.

## Model

The default tutor is **qwen3:14b**, with thinking off except where noted below. qwen3:8b was too weak: it served wrong answers marked as verified. Screenshots are read by **gemma4**.

Speed depends heavily on power mode. Low Power Mode cut qwen3:14b from about 14 to 6 tokens per second.

## How answers are checked

### Generated problems (`create_problem`)

1. **Draft.** The model writes the problem, its own answer, and code that computes the answer. The code runs in the sandbox.
2. **Blind solve.** The model solves the problem again *without being told the answer*.
3. **Decision.** The problem is served only if all three routes agree. If they disagree, the model is asked to reconcile. A problem still disputed after the last retry is discarded, never shown.

### Imported problems (`import_problem`)

These are never discarded.

- **With the book's answer:** the book's answer is the truth. If the blind solution disagrees, the model is shown the book's answer and asked to find its own mistake (`prompts/correct.md`).
- **Without it:** the three routes vote, and any disagreement is shown honestly in the note.

### Lessons from testing (each fix has a test or a comment in the code)

- **Disagreement is not resolved by letting the code win.** The code sometimes answers a different question.
- **The solve must be blind.** Told the answer, the model noticed the contradiction and then fudged its working to match.
- **Plain-text sections, not JSON.** Models write output as `## Heading` sections (`app/sections.py`), because LaTeX inside JSON broke on unescaped backslashes.
- **No floats, numeric AMC answers.** Checking code must use exact arithmetic, and AMC answers must be numbers.
- **"+ C" is ignored** when comparing answers.

## Guards on what the student sees

Models that know the answer state it, even when told not to (for example, "8 instead of the correct 9"). `app/leak.py` handles this in code. While a problem is unsolved:

- **Hints, error analysis and "Why?" are written hidden**, then checked for the answer's value in the tutor's own words. Text quoted from the student is ignored. If the answer appears, the model rewrites the reply; if it still appears, the value is replaced with "[hidden]". Values the student has written themselves (their working, and for error analysis their answer) never count. A plain fraction like -5/2 is read as one value.
- **Set answers** (such as a polynomial's roots) guard each element that is not an integer from −10 to 10. Small integers are not guarded one by one ("degree 3" and "2 roots" are ordinary phrasing); naming every element of the set counts as a leak. Interval answers are not guarded.
- **Hints are also checked for values the student hasn't reached** (`leak.ahead`), such as "factor pairs of 35" before the student has got to 35. Small integers 0–10 are allowed. If found, the hint is rewritten.

Two more choices settled by testing:

- **Error analysis always uses thinking.** Without it, qwen3:14b blamed a correct step instead of the real mistake (2 of 2 runs). With thinking and the audit prompt it found the real mistake 5 of 5 times, in 24–52 s.
- **Proof review also uses thinking.** It caught both gaps in a flawed proof.

## Help-loop test results (qwen3:14b, Low Power Mode off, on battery)

| Step | Result |
|---|---|
| Screenshot transcription (gemma4) | Perfect in 7 s, including a messy option layout |
| Import with book answer | 79 s; about 6 min when the tutor must correct itself (AIME 1999 #3) |
| Import without book answer | 133 s; the vote kept the right answer when the model's own was wrong |
| Answer check | Instant (SymPy) |
| Error analysis | Fixed (see above) |
| "Why?" | Good, about 50 s |
| Flag review | Correct verdict |
| Proof review | Accepts a valid proof and catches a flawed one; 60–145 s |
| Hints | Being fixed: they jumped ahead and did steps for the student. Prompt tightened and `leak.ahead` added; retest in progress |

## Book reader

A **Book tab** for working through a printed book chapter in the app. Chapter 7 of AoPS Intermediate Algebra (157 problems) is transcribed.

### What's built

- **Book tab:** a book, then a chapter, then its sections, with the book's text and figures verbatim.
- **Hidden in-text solutions:** solutions, answers and hints never reach the browser until the student asks. Solution-only images are 404 until an attempt has seen the solution or solved the problem.
- **Book hints first:** a problem's own hints are served instantly and verbatim; the tutor's rungs follow them. The ladder holds 3 hints.
- **Self and proof problems:** `self` problems show the solution and the student marks "got it"; `proof` problems go to proof review. Only an open `self` attempt can be self-marked.
- **Shared solutions:** when parts share one solution (`covers`), showing it on one part records it on the others too, with the solution added to each part's attempt. A part already solved is left alone. A `self` part stays open, so the student can still mark it. Any other open part is given up, which adds a gave_up attempt to Progress for each such part.
- **Reading after solving:** once a book problem is solved, reading its solution costs nothing: an unaided solve stays unaided.
- **Progress tagging:** attempts carry the chapter's tags (AMC / Algebra / Polynomials).

### Adding a chapter

Content lives in the git-ignored `books/<book>/<chapter>/`: `chapter.yaml` (number, title, tags, section files), one YAML per section (ordered `prose`, `box` and `problem` blocks), and `img/`. Each problem has `ref`, `kind`, `statement`, `hints`, `solution`, `solution_by`, `answer_type`, `answer`, `tutor_hints`. The format is described in `docs/superpowers/plans/2026-10-23-book-reader.md`. Transcribe online with Claude, verbatim, then check with `tests/test_book_content.py`. No book text or images are ever committed.

Transcription flags raised from the app are in `db.book_reports()`. There is no page for them yet; list them with `.venv/bin/python -c "from app import db; print(db.book_reports())"`.

### Live check (qwen3:14b, throwaway DB, 2026-10-24)

Every call returned. No full stored answer was stated, but there were partial leaks: the root −5/2 on 7.12(f) (set answers were unguarded then; they are now guarded), "two crossings near x = 1" on 7.47, and the count 6 on 7.51. The last two are error-analysis model quality, which the guard doesn't cover. One leak-guard rewrite fired.

| Problem | Call | Time | Leak | Rewrite | Quality |
|---|---|---|---|---|---|
| 7.12(f) intext | hint 1 (typed working) | 15 s | no | no | good Socratic nudge |
| | hint 2 | 13 s | no | no | fine, generic |
| | hint 3 | 14 s | no | no | fine; repeats student's own root |
| | wrong answer, error analysis | 52 s | one root (-5/2) named | no | found the sign slip correctly |
| | Why? | 42 s | no | no | accurate |
| 7.2.1(b) exercise | hint 1 | 5 s | no | no | good |
| | hint 2 | 7 s | no | no | bad: also printed "Hint 2:" and "Hint 3:" lines |
| | hint 3 | 6 s | no | no | good |
| | error analysis (answer `2, 3, 5`) | 49 s | no | no | found 5 vs -5 but muddled |
| | Why? | 38 s | no | no | accurate |
| 7.47 challenge | hint 1 | 7 s | no | no | good |
| | hint 2 | 9 s | no | no | generic |
| | hint 3 | 12 s | no | no | bad: "Hint 2:/Hint 3:" lines again |
| | error analysis (answer `3`) | 52 s | no (4 not stated) | no | right idea, but gives away "two crossings near x = 1" |
| | Why? | 46 s | no | no | accurate, hints at the same crux |
| 7.51 challenge | hint 1 | 0 s | no | no | the book's hint, verbatim |
| | hint 2 | 11 s | no | no | generic |
| | hint 3 | 13 s | no | no | bad: "Hint 2:/Hint 3:" lines again |
| | error analysis (answer `80`) | 91 s | no (96 not stated) | yes, "Rewording so it doesn't give the answer away" | found the miscount, but states the right count of 6 |
| | Why? | 44 s | no | no | accurate |
| 7.21 self | solution, then "got it" | instant | n/a | n/a | marked correct |
| 7.20 proof | proof review (valid proof) | 57 s | n/a | n/a | poor: called a valid proof incomplete |

Also: answer `2, 3, -5` for 7.2.1(b) is marked correct, because sets ignore repeated roots (a deliberate choice, see above).

Problems seen: a hint call sometimes returned several hints at once ("Hint 2: ...", "Hint 3: ..." appended), so the student saw later rungs early (3 of 11 tutor hints). Fixed since: the hint prompt now shows the model only the current level's rule and asks for one hint with no label, and the reply is cut at any later "Hint N:" label. Not yet retested live. Error analysis sometimes names a step value or the key idea. Proof review can raise a pedantic gap in a correct proof.

### Known limitations

- Some answers are self-marked because the verifier has no ordered-tuple type: 7.39, 7.48, 7.58, 7.6.4.
- 7.44 marks a scaled-but-equivalent equation as wrong.
- A book's 4th and later hints are not shown (the ladder holds 3).
- 7.38(c)'s solution is written by the tutor (the source had none) and is labelled so.

## Not yet done

- [ ] Finish the hint retest (fresh import with the new hint rules).
- [ ] Test offline (Wi-Fi off): `./run.sh --check`, then a full import and hint cycle.
- [ ] Hand-check a few generated problems again on the final pipeline.
- [ ] Optional, the student passed for now: show a problem early while the blind solve runs, and pre-generate problems.

## Dev tips

- Use a throwaway database: `THEMATHS_DATA=/tmp/tm ./run.sh`, or a second server on another port.
- Prompts are read from disk on every call, so editing a prompt changes a running server immediately. Python changes need a restart.
- Don't use `uvicorn --reload` while a stream is open: the reload hangs.
- Unusable model replies and failing checking code are saved to `data/debug/`.
