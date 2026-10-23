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

- **Hints, error analysis and "Why?" are written hidden**, then checked for the answer's value in the tutor's own words. Text quoted from the student is ignored. If the answer appears, the model rewrites the reply; if it still appears, the value is replaced with "[hidden]".
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
