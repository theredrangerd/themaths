# Status: where v1 stands (2026-10-19)

Working notes for picking the build back up. SPEC.md is the target; this file is the gap.

## Working

- Backend (FastAPI): generation, screenshot transcription and import, answer checking, the hint ladder, error analysis, "Why?", flagging, progress, history, export, settings. Run `./run.sh`; check offline readiness with `./run.sh --check`.
- Verifier (`app/verify.py`): equivalent forms, decimals to 3+ significant figures, multiple choice by letter or value, sets, intervals. Tested.
- Sandbox (`app/sandbox.py`, `app/sandbox_runner.py`): runs model-written checking code in a separate process with an import whitelist, banned builtins, a CPU limit and a timeout. Rejects `result = <literal>`. Tested.
- JSON repair (`app/jsonfix.py`): models write `\frac` unescaped inside JSON, which the parser reads as a form feed plus "rac". Fixed and tested.
- Frontend: AoPS-style layout in light and dark, working at phone width. Checked by screenshot: empty state, problem view, check and hint flow, progress page.
- `pytest`: 32 tests pass.

## Generation pipeline (`app/tutor.py`, `create_problem`)

Two stages:

1. **Draft.** The model returns JSON with the problem, its own answer, and checking code. The code runs in the sandbox.
   - If the answer and the code agree: `verified`.
   - If they disagree: the computed value wins, and the status is `computed`.
   - If the code fails: `unverified`.
2. **Finish.** The model writes the solution, the hints, and for AMC four distractors, all aimed at the settled answer. The app sorts the five AMC options and assigns the letter.

## Benchmark findings (stage 1 only)

Ollama's defaults caused the early failures: a 4096-token context and no output cap. That produced 10-minute hangs and endless JSON whitespace. `app/ollama.py` now sets `num_ctx` to 16384 and caps `num_predict`.

| Config | Time | Result |
|---|---|---|
| qwen3:8b, no thinking | 9–18 s | OK. The IB integral answer was wrong (π/4 − 1/4) and SymPy corrected it to π/4. |
| gemma4, no thinking | n/a | JSON string looped until the length cap on both requests |
| gemma4, thinking | 55 s | Best AMC problem, but its "checking code" hardcoded `result = 26` (now rejected). IB run looped. |
| qwen3:8b, thinking | not finished | The run was interrupted |

The default is now `think: false` with qwen3:8b.

**Main weakness:** with an 8B model, problem quality is mediocre. Problems can be dull or mis-tagged, and sometimes the checking code doesn't model the problem, in which case a `computed` answer can be wrong (seen once). A bigger model is the real fix. Try `qwen3:14b` (about 9 GB), or `qwen3:30b-a3b` if memory allows.

## Not yet done or verified

- [ ] End-to-end run through the UI of a fresh generation since the two-stage rewrite. Each stage was tested separately; the full flow via `/api/problems/generate` was not re-run after the last changes (jsonfix, the computed-answer form, hardcoded-result rejection).
- [ ] Tested in the browser with a real model reply: hints with typed working, error analysis after a wrong answer, "Why?", flagging, proof review.
- [ ] Screenshot transcription with gemma4: not exercised yet.
- [ ] Spec acceptance criterion "run with Wi-Fi off": not tested yet.
- [ ] Finish the qwen3:8b-with-thinking benchmark, then decide whether thinking should apply to stage 1 only.
- [ ] Problem variety: the `avoid` list helps, but qwen3:8b still repeats "how many integers less than 1000…"-style problems.

## Dev tips

- Use a throwaway database: `THEMATHS_DATA=/tmp/tm ./run.sh`
- Don't use `uvicorn --reload` while a stream is open: the reload hangs until the stream closes.
