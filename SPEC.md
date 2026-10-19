# themaths — Specification (v1)

An offline, local-first maths tutor that runs on a MacBook (Apple M5, 24 GB RAM) against models served by Ollama. Its purpose is **conceptual understanding**, not answer delivery: it withholds solutions by default, guides with graduated hints, verifies answers deterministically, and records progress locally so growth is visible over time.

---

## 1. Context and goals

**User.** A single user studying IB Mathematics: Analysis & Approaches HL and preparing for the AMC 12.

**Why local.** Much of the studying happens with no internet access. When online, the user will use Claude directly; this app exists for the offline case. Every runtime dependency must therefore work with no network.

**Goals**

1. Generate and present practice problems on request, at a chosen or inferred topic and difficulty.
2. Withhold the answer by default and guide the user with a graduated hint ladder.
3. Mark answers by deterministic verification (SymPy / numeric), not by the model's opinion.
4. When an answer is wrong, point out where the user's working appears to have gone wrong.
5. Explain concepts on demand ("Why?"), using the current problem as the illustrative example.
6. Accept screenshots of printed problems and transcribe them to LaTeX.
7. Persist progress locally and present per-topic growth over time.

**Non-goals for v1**

- Graphs and interactive variables (planned; see §10).
- Photos of handwritten working.
- Adaptive "choose my next problem" mode.
- Multi-user support, accounts, sync, or any network service.

---

## 2. Constraints

| Constraint | Implication |
|---|---|
| Fully offline at runtime | No CDNs. KaTeX and all frontend assets are vendored into the repo. Python dependencies are installed while online. |
| Local models (initially `qwen3:8b`, `gemma4:latest`) | Models make arithmetic and algebra errors; correctness must not depend on them alone (§6). |
| Slow but careful is acceptable | Responses are streamed; latency of 30–60 s+ for reasoning is tolerated. |
| Battery-conscious | No background model calls. The model runs only in response to a user action. |
| Single user | No auth. Server binds to `127.0.0.1` only. |

---

## 3. Architecture

```
Browser (localhost)                 Python server (FastAPI)             Ollama (localhost:11434)
┌───────────────────────┐   HTTP    ┌──────────────────────────┐  HTTP  ┌──────────────────┐
│ Vanilla HTML/CSS/JS    │ ───────▶ │ Session + prompt engine  │ ─────▶ │ qwen3 / gemma4 / │
│ KaTeX (vendored)       │ ◀─────── │ Verifier (SymPy sandbox) │ ◀───── │ any pulled model │
│ Live LaTeX preview     │  stream  │ Progress store (SQLite)  │        └──────────────────┘
└───────────────────────┘           └──────────────────────────┘
```

- **Backend:** Python 3.12+, FastAPI, Uvicorn, `httpx` for the Ollama API, SymPy for verification, SQLite (stdlib `sqlite3`) for storage.
- **Frontend:** Plain HTML/CSS/JS served by the backend. No build step. KaTeX (with auto-render) vendored under `static/vendor/katex/`.
- **Streaming:** Model output is streamed to the browser (Server-Sent Events) and rendered incrementally.
- **Launch:** One command (e.g. `./run.sh`) starts the server and opens the browser.

---

## 4. Models

- Configured in a settings panel and persisted to the local database; no code change required to switch.
- Two roles:
  - **Reasoning model** — problem generation, hints, explanations, error analysis. Default `qwen3:8b`.
  - **Vision model** — screenshot transcription. Default `gemma4:latest` (confirm image support with `ollama show gemma4`).
- The app lists installed models via the Ollama API and warns if a configured model is missing or Ollama is not running.
- Recommended upgrade while online: `qwen3:14b` (~9 GB) or `qwen3:30b-a3b` (MoE; fast for its size). Both fit in 24 GB alongside the app.
- The app does not use custom Modelfiles; all prompting lives in the app's prompt templates (`prompts/`).

---

## 5. Core loop

### 5.1 Requesting a problem

The user requests a problem in natural language, e.g. *"hard AMC 12 counting problem"*, *"IB HL complex numbers, paper 1 style"*, *"something on the chain rule that's not routine"*. Optional controls (track, topic, difficulty) prefill or override the request; free text always works. When the request is under-specified, the model chooses sensibly and the chosen tags are shown.

Alternatively, the user pastes or drops a **screenshot** of a printed problem. The vision model transcribes it to LaTeX; the user can correct the transcription before proceeding. Screenshotted problems may lack a known answer; see §6.4.

### 5.2 Generation contract

For each generated problem the model returns a structured (JSON) payload; only the statement is shown:

| Field | Purpose |
|---|---|
| `statement` | Problem text in Markdown + LaTeX. Shown to the user. |
| `track`, `topic`, `subtopic`, `difficulty` | Tags from the fixed taxonomy (§8). |
| `answer` | Final answer in canonical form. Hidden. |
| `answer_type` | `exact_expression`, `numeric`, `integer`, `multiple_choice`, `set`, `interval`, `proof`. |
| `choices` | For AMC-style multiple choice (A–E). |
| `verification` | A short SymPy/Python snippet that recomputes the answer independently. Hidden. |
| `hint_ladder` | Pre-drafted hints for levels 1–3. Hidden until requested. |
| `solution` | Full worked solution. Hidden until level 4. |

If the payload fails to parse, or `verification` disagrees with `answer`, the server regenerates (up to N retries, default 2). If it still fails, the problem is shown with a visible **"unverified"** badge.

### 5.3 Working area

A text box for the user's working and final answer, accepting LaTeX or plain text, with a live KaTeX preview. Actions:

| Action | Behaviour |
|---|---|
| **Check answer** | Marks via the verifier (§6). On correct: confirm, offer the full solution for comparison. On incorrect: say so, then give **error analysis** — where the working appears to have gone wrong — clearly labelled as the tutor's reading, distinct from the verified verdict. Error analysis points to the faulty step and the reason, but does not supply the corrected answer. |
| **Hint** | Advances one level up the hint ladder (§5.4). Hints are conditioned on the user's typed working when present. |
| **Why?** | Expository explanation of the underlying concept, using this problem only as the illustrative example. |
| **Another like this** | A new problem that is thematically related but distinct. |
| **Show solution** | Equivalent to jumping to hint level 4; requires a confirmation click. |

### 5.4 Hint ladder

| Level | Content |
|---|---|
| 1 — Nudge | Redirects attention: what to notice, what to ask. ("Consider the symmetry of the expression.") |
| 2 — Tool | Names the technique, theorem, or property that applies. |
| 3 — Next step | States the next concrete step, without completing it. |
| 4 — Solution | The full worked solution. |

The highest level reached is recorded per attempt (§7). When the user has typed working, hints at levels 1–3 are tailored to where that working stands, using the pre-drafted ladder as a reference.

---

## 6. Verification

The central rule: **the model never decides on its own whether the user is right.**

### 6.1 Answer checking

1. Parse the user's final answer (LaTeX → SymPy via `sympy.parsing.latex`, falling back to `parse_expr` on plain text).
2. Compare to the stored answer according to `answer_type`:
   - `exact_expression`: `simplify(user - answer) == 0`, then numeric spot-checks at random points if symbolic simplification is inconclusive.
   - `numeric`: relative tolerance (default 1e-9; problem may specify rounding, e.g. 3 s.f. for IB).
   - `integer`, `multiple_choice`: exact match.
   - `set`, `interval`: structural equality after canonicalisation.
   - `proof`: not machine-checkable; falls back to model review, clearly labelled **"not verified"**.
3. If the user's answer cannot be parsed, ask them to restate it rather than guessing.

### 6.2 Verification snippets

Model-written verification code runs in a **restricted subprocess**: SymPy/math imports only, no file or network access, time limit (default 5 s), memory limit. Its output must match `answer`.

### 6.3 Ill-formed problems

Since problems are model-generated, some will be wrong. A **"This problem seems wrong"** button lets the user flag one and say why; the tutor then reviews it. Flagged problems are recorded and excluded from accuracy statistics. (Catching a broken problem is itself evidence of understanding and is logged as such.)

### 6.4 Screenshotted problems

The model proposes an answer and verification snippet as in §5.2. Where verification fails, the problem proceeds with an **"unverified"** badge and marking is labelled accordingly.

---

## 7. Progress tracking

Stored in a local SQLite file (`data/themaths.db`, git-ignored).

**Per problem:** statement, tags, answer, verification status, source (`generated` / `screenshot`), created time.

**Per attempt:** problem, outcome (`correct` / `incorrect` / `gave_up` / `flagged`), number of answer submissions, highest hint level reached, whether the solution was viewed, time spent, the user's final working.

**Progress page**

- Per-topic accuracy and average hint level, over time.
- Weak topics highlighted (low accuracy or high hint dependence, with a minimum sample size).
- Separate views for IB and AMC.
- History: browse and reopen past problems and attempts.

**Export:** one-click JSON/CSV export of all data.

---

## 8. Topic taxonomy

Fixed lists, editable in a config file (`taxonomy.yaml`).

**IB AA HL**

1. Number and algebra (sequences and series, logarithms, proof, complex numbers, binomial theorem, systems of equations)
2. Functions (properties, transformations, polynomials, rational functions, inequalities)
3. Geometry and trigonometry (trig identities and equations, vectors, lines and planes)
4. Statistics and probability (probability, distributions, Bayes, continuous random variables)
5. Calculus (limits, differentiation, integration, differential equations, Maclaurin series)

**AMC 12**

1. Algebra (polynomials, functional equations, inequalities, sequences)
2. Counting and probability
3. Number theory
4. Geometry (plane, coordinate, solid)
5. Trigonometry and complex numbers

**Difficulty**

- IB: `straightforward` → `standard` → `exam-hard` → `beyond-syllabus`
- AMC: problem-number bands `1–10`, `11–20`, `21–25`

---

## 9. Tutor voice and presentation

- Precise, formal, concise prose; no filler. AoPS/textbook register.
- Mathematics is the focus: display equations for key steps, aligned derivations (`aligned`), consistent notation.
- Each substantive response has a short framing line, the body, and a closing suggestion for the next step.
- Prompts live in `prompts/` as editable templates, one per intent: `generate`, `hint`, `explain`, `error_analysis`, `transcribe`, `review_flag`.
- Clean, distraction-free UI; serif body type suited to mathematical reading; light and dark themes.

---

## 10. Future phases

- **Phase 2 — Visual insight.** In conceptual explanations, the model may emit a fenced plot spec (e.g. a function with parameters) that the frontend renders with a vendored plotting library. The response format reserves a block type for this from v1.
- **Phase 3 — Dynamic control.** Sliders bound to parameters in plot specs for live exploration.
- **Adaptive practice.** Use progress data to propose the next problem from weak areas.
- **Handwritten working.** Photo input once vision models are reliable enough.

---

## 11. Setup (while online)

1. Install Python dependencies: `pip install -r requirements.txt` (FastAPI, Uvicorn, httpx, SymPy, antlr4 runtime for LaTeX parsing).
2. KaTeX is vendored in the repo — no download needed at runtime.
3. Ensure models are pulled: `ollama pull qwen3:8b`, `ollama pull gemma4` (optionally `qwen3:14b`).
4. Verify offline readiness: a `./run.sh --check` command confirms dependencies, Ollama reachability, and model availability.

---

## 12. Acceptance criteria for v1

- [ ] With Wi-Fi off, the app launches, generates a problem, and renders LaTeX correctly.
- [ ] Generated problems hide answer, hints, and solution until requested.
- [ ] Correct and incorrect answers are marked by the verifier, with the verification status visible.
- [ ] Incorrect answers receive error analysis labelled as the tutor's reading.
- [ ] The hint ladder advances one level per request and is tailored to typed working.
- [ ] "Why?" produces a concept explanation anchored to the current problem.
- [ ] A screenshot of a printed problem is transcribed to editable LaTeX.
- [ ] Attempts are persisted; the progress page shows per-topic accuracy and hint usage.
- [ ] Switching the reasoning model in settings takes effect without restarting.
