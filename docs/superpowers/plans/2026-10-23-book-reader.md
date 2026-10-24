# Book Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Book section that presents Chapter 7 of AoPS *Intermediate Algebra* verbatim, with in-text solutions hidden and the existing tutor panel on every problem.

**Architecture:** Book content is YAML under a git-ignored `books/` folder, read by a new loader (`app/books.py`). A book problem becomes an ordinary `problems` row (`source = 'book'`) when first opened, so the existing attempt, hint, check, analyse, explain and flag machinery runs unchanged apart from four targeted changes: book hints served verbatim, `self` problems, sibling "solution seen", and secret solution images. The frontend gains a Book view and reuses the Practice tutor elements by moving them into the book page.

**Tech Stack:** Python 3.12+, FastAPI, SQLite, PyYAML (already a dependency), vanilla JS, KaTeX via `static/render.js`. Tests: pytest (`.venv/bin/python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-10-23-book-reader-design.md`

## Global Constraints

- Offline at runtime. No new runtime dependencies. PyMuPDF and Pillow are for transcription only, in a scratch venv, never in `requirements.txt`.
- `books/` is git-ignored. No book text or images are ever committed, including in test fixtures: fixtures use invented text.
- Book text, problems, hints and solutions are verbatim. Only formatting changes. Online-book interface text ("You may type any additional notes", "Hide Solution", "Reset", "Your Submission", "Preview: Solution") is not content.
- Solutions, answers, book hints and tutor hints never reach the browser before the student asks for them. That covers the chapter payload, and images inside solutions.
- Same look: existing `static/app.css` tokens and components. No new visual style.
- Chapter 7 tags: track `AMC`, topic `Algebra`, subtopic `Polynomials`.
- A Claude-written solution (only where the screenshots lack one) is labelled "Solution by the tutor, not the book".

## Review Focus

1. **Odd or hostile URL parts.** A ref like `7.23(b)`, or `..`, or `%2F` in book, chapter or image names: refs must resolve, and anything else must 404 without touching files outside `books/`. Test in Task 4.
2. **The chapter payload leaking.** Solution, answer, book hints or tutor hints in `GET /api/books/...`, or a solution image fetched without an unlocked attempt: these must be absent or 404. Tests in Tasks 1 and 4.
3. **Content edited after attempts exist.** A transcription fix: the row updates in place, attempts and history survive, and the flag clears. Test in Task 2.
4. **An existing `data/themaths.db` without the new columns.** It migrates on start, and old rows still load. Test in Task 2.
5. **LaTeX in YAML.** Backslashes, colons and `#` in statements must survive loading byte for byte. A malformed file must give an error naming the file and the ref, not a 500 with a stack trace. Test in Task 1.

---

## File map

| File | Responsibility |
|---|---|
| `app/books.py` (new) | Find, load and validate book content. Build public views, DB rows and hint ladders. Image paths. |
| `app/config.py` | `BOOKS_DIR` (env `THEMATHS_BOOKS`, default `ROOT / "books"`) |
| `app/db.py` | New columns and migration, `upsert_book_problem`, `latest_attempt`, `book_statuses`, `book_reports` |
| `app/tutor.py` | `open_book_problem`, `retry`, `self_mark`; book-hint and `self` and sibling changes in `hint`/`solution` |
| `app/verify.py`, `app/leak.py` | Answer type `self` |
| `app/main.py` | `/api/books...` routes, `/retry`, `/self`, image route |
| `static/index.html`, `static/app.js`, `static/app.css` | Book nav, list, chapter page, tutor placement, new controls |
| `tests/test_books.py`, `tests/test_book_db.py`, `tests/test_book_tutor.py`, `tests/test_book_api.py` (new) | Unit and route tests on an invented fixture book |
| `tests/test_book_content.py` (new) | Checks the real content when `books/` exists, and skips otherwise |
| `tests/fixtures/books/demo/01/...` (new) | Tiny invented book, committed |
| `books/intermediate-algebra/...` (git-ignored) | Chapter 7 content and images |

## Content format (fixed here so every task agrees)

```
books/<book>/book.yaml             title: Intermediate Algebra
                                   chapters: ["07"]
books/<book>/<chapter>/chapter.yaml
    number: 7
    title: Polynomial Roots Part I
    tags: {track: AMC, topic: Algebra, subtopic: Polynomials}
    sections: [7-1.yaml, 7-2.yaml, ..., 7-7.yaml, review.yaml, challenge.yaml]
books/<book>/<chapter>/<section>.yaml
    number: "7.1"             # or "Review Problems" / "Challenge Problems"
    title: The Factor Theorem
    star: false
    blocks:                   # ordered; each block is exactly one of:
      - prose: |              # Markdown + LaTeX
      - box: important        # important | concept | warning | sidenote | extra
        text: |
      - problem:
          ref: "7.5"          # unique in chapter; parts as "7.23(b)"
          kind: intext        # intext | exercise | review | challenge
          preamble: |         # optional shared stem, shown with every part
          statement: |
          hints: []           # the book's, verbatim, in order
          solution: |
          solution_by: book   # book | tutor
          answer_type: set    # any verify type, or proof, or self
          answer: "-5, -2/3, 1, 3"   # "" for proof and self
          tutor_hints: []     # Claude's; fill rungs after the book's
          covers: []          # optional: refs sharing an unsplittable solution
books/<book>/<chapter>/img/<name>.png    referenced in text as ![](img/<name>.png)
```

Slugs (book, chapter) match `^[a-z0-9-]+$`. Image names match `^[\w-]+\.(png|jpg|svg)$`.

---

### Task 1: Content loader

**Files:**
- Create: `app/books.py`, `tests/test_books.py`, `tests/fixtures/books/demo/book.yaml`, `tests/fixtures/books/demo/01/chapter.yaml`, `tests/fixtures/books/demo/01/1-1.yaml`, `tests/fixtures/books/demo/01/img/fig.png` (any small PNG), `tests/conftest.py`
- Modify: `app/config.py` (add `BOOKS_DIR`), `.gitignore` (add `books/`)

**Interfaces:**
- Produces:
  - `class BookError(ValueError)`: message names the file and, where relevant, the ref
  - `list_books() -> list[dict]`: `[{"slug", "title", "chapters": [{"slug", "number", "title"}]}]`
  - `load_chapter(book: str, chapter: str) -> dict`: `{"book", "slug", "number", "title", "tags", "sections": [{"number", "title", "star", "blocks": [...]}]}`; problem blocks are `{"problem": {...all fields, defaults filled}}`. Raises `BookError` for bad content and `LookupError` for unknown or invalid slugs.
  - `public_chapter(ch: dict) -> dict`: the same shape, but each problem keeps only `ref, kind, preamble, statement, answer_type, solution_by`. Image links are rewritten to `/api/books/{book}/{chapter}/img/{name}`.
  - `find_problem(ch: dict, ref: str) -> dict` (raises `LookupError`)
  - `ladder(problem: dict) -> tuple[list[str], int]`: `(rungs, book_count)`. Rungs are `hints[:3] + tutor_hints`, cut to 3, and `book_count = min(len(hints), 3)`.
  - `problem_row(ch: dict, problem: dict) -> dict`: the dict for `db.upsert_book_problem` (Task 2):
    - `source="book"`
    - `book_ref=f"{book}/{chapter}/{ref}"`
    - `statement` = preamble + blank line + statement
    - `hints`, `book_hints` from `ladder`
    - `solution` with image links rewritten as in `public_chapter`
    - `answer_type`, `answer`
    - `track`/`topic`/`subtopic` from `tags`
    - `verification_status` = `"from_book"`, or `"tutor_solution"` when `solution_by == "tutor"`, or `"not_applicable"` for proof
    - `content_hash` = sha256 of the JSON of those fields
  - `secret_images(ch: dict) -> set[str]`: image names referenced in any solution
  - `image_path(book: str, chapter: str, name: str) -> Path` (raises `LookupError` for bad slugs or names, or a missing file)
  - `siblings(book_ref: str) -> list[str]`: the `covers` refs of that problem, excluding itself, as full book_refs

- [ ] **Step 1: Write the fixture book.** Use invented text. Section `1.1` has one prose block, with LaTeX containing `\frac`, `#`, and a `: ` inside a block scalar. It has one `important` box, and these problems:
  - `1.1`: intext, two book hints, integer answer `7`
  - `1.1.1(a)` and `1.1.1(b)`: exercises sharing a preamble, with `covers: ["1.1.1(a)", "1.1.1(b)"]` on both
  - `1.1.2`: self, with solution text containing `![](img/fig.png)`
  - `1.1.3`: proof
  - `1.1.4`: integer, `solution_by: tutor`, four book hints

  `tests/conftest.py` sets `THEMATHS_BOOKS` to `tests/fixtures/books` and `THEMATHS_DATA` to a `tmp_path` per test, then reloads `app.config`. Do whatever the existing modules need so they pick these up. Put that in one fixture, `book_env`, which later tasks use.
- [ ] **Step 2: Write failing tests in `tests/test_books.py`:**
  - `test_loads_fixture_verbatim`: the prose block equals the exact string in the YAML, with backslashes, `#` and `: ` intact
  - `test_public_chapter_hides_secrets`: `json.dumps(public_chapter(ch))` contains no solution text, answer, book hint or tutor hint from the fixture
  - `test_ladder_book_hints_first`: for `1.1`, `ladder` gives `(book1, book2, tutor1)` with count 2; for `1.1.4`, the first 3 book hints with count 3
  - `test_problem_row_statuses`: `1.1.4` gives `tutor_solution`, `1.1.3` gives `not_applicable`, `1.1` gives `from_book`; the statement of `1.1.1(b)` starts with the preamble
  - `test_hash_changes_with_content`: editing a statement (in a copied tmp book) changes `content_hash`
  - `test_secret_images`: `secret_images(ch) == {"fig.png"}`
  - `test_bad_content_names_file_and_ref`: a copied book with a duplicate ref, an unknown `answer_type`, and a missing `statement` (one at a time) each raise `BookError` whose message contains the file name and the ref
  - `test_bad_slugs`: `load_chapter("../x", "01")`, `image_path("demo", "01", "../../app/db.py")` and `image_path("demo", "01", "nope.png")` raise `LookupError`
  - `test_siblings`: `siblings("demo/01/1.1.1(a)") == ["demo/01/1.1.1(b)"]`
- [ ] **Step 3: Run** `.venv/bin/python -m pytest tests/test_books.py -v`. Expected: FAIL (no module `app.books`).
- [ ] **Step 4: Implement `app/books.py`** with the interface above. Use `yaml.safe_load`, and validate with plain checks. There's no caching, because the files are small and edits must show up without a restart.
- [ ] **Step 5: Run** the same command. Expected: PASS. Then run the whole suite. Expected: PASS.
- [ ] **Step 6: Commit.** Use `git add app/books.py app/config.py .gitignore tests/`, then commit "Load book content from YAML".

### Task 2: Database support for book problems

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_book_db.py`

**Interfaces:**
- Consumes: `books.problem_row` dicts (Task 1)
- Produces:
  - `problems` gains `book_ref TEXT`, `book_hints INTEGER NOT NULL DEFAULT 0`, `content_hash TEXT`, and a unique index on `book_ref`. `init()` adds missing columns to existing databases with `ALTER TABLE`.
  - `upsert_book_problem(row: dict) -> int`: inserts if new. If `content_hash` differs, it updates the content fields and sets `flagged = 0`. Returns the problem id. Attempts are never touched.
  - `problem_by_ref(book_ref: str) -> dict | None`
  - `latest_attempt(problem_id: int) -> int | None`
  - `book_statuses(prefix: str) -> dict[str, str]`: for each book_ref starting with `prefix`, the best status over its attempts. In order: `"unaided"` (correct, hint_level 0, solution not viewed), `"solved"` (correct), `"seen"` (solution viewed), `"started"`. Problems never opened are absent.
  - `book_reports() -> list[dict]`: flag events on book problems, as `{book_ref, reason, created_at}`
  - `get_attempt` returns the new columns as well

- [ ] **Step 1: Write failing tests:**
  - `test_upsert_is_idempotent`: same row twice gives the same id and one row
  - `test_content_fix_updates_row_keeps_attempts_clears_flag` (Review Focus 3): insert, start an attempt, flag it, then upsert with a changed statement and hash. Same id, new statement, `flagged == 0`, and the attempt still there.
  - `test_migrates_old_database` (Review Focus 4): create a DB using the pre-change `problems` schema (copy today's `CREATE TABLE problems` statement into the test as a literal), insert a row, run `db.init()`. The columns exist and the old row loads via `get_attempt`.
  - `test_book_statuses_best_outcome`: attempts gave_up, then correct with a hint, gives `"solved"`; a correct attempt with no hints and no solution gives `"unaided"`; an opened attempt with no action gives `"started"`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_book_db.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in `app/db.py`, matching its existing style (`tx()`, plain SQL).
- [ ] **Step 4: Run** the tests, then the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** "Store book problems with a stable reference".

### Task 3: Tutor behaviour for book problems

**Files:**
- Modify: `app/tutor.py`, `app/verify.py`, `app/leak.py`
- Test: `tests/test_book_tutor.py`

**Interfaces:**
- Consumes: `books.load_chapter`, `find_problem`, `problem_row`, `siblings` (Task 1); `db.upsert_book_problem`, `latest_attempt`, `problem_by_ref` (Task 2)
- Produces:
  - `open_book_problem(book: str, chapter: str, ref: str) -> dict`: upserts the row, resumes its latest attempt or starts one, and returns `attempt_state`
  - `retry(attempt_id: int) -> dict`: a new attempt on the same problem, returning its state
  - `self_mark(attempt_id: int, got_it: bool) -> dict`: only allowed once `solution_viewed`, else `ValueError`. Finishes the attempt as `correct` or `gave_up`.
  - `hint`: if `level <= problem["book_hints"]`, it returns `problem["hints"][level-1]` unchanged as a `done` event, with no model call and no leak checks, even when working is typed. The event meta is `{"book": True}`.
  - `solution`:
    - For `answer_type == "self"`, it sets `solution_viewed = 1` and `hint_level = 4` without finishing.
    - For any book problem with `siblings`, every sibling gets an attempt (its latest, or a new one) with `solution_viewed = 1`, finished as `gave_up` if still open.
    - Image links in the served solution get `?attempt={id}`.
  - `_public` also returns `book_ref` and `book_hints` in `problem`
  - `verify.check`: `self` gives `Verdict("unverifiable", ...)`. `leak.secret_values`: `self` gives `[]`.

- [ ] **Step 1: Write failing tests.** Use the `book_env` fixture. Stub the model stream (monkeypatch `tutor._stream`) so that any model call fails the test, unless the test expects one.
  - `test_open_resumes_latest_attempt`: opening twice gives the same attempt_id; after `retry`, opening again gives the new one
  - `test_book_hints_verbatim_even_with_working`: on `1.1`, hint 1 with working `"I tried x = 7"` returns book hint 1 exactly, with no model call. Hint 3 calls the model (the stub records the call).
  - `test_book_hint_not_rewritten_when_it_names_answer`: a fixture book hint containing `7` (the answer) is returned unchanged
  - `test_self_problem_reveal_then_mark`: on `1.1.2`, `self_mark` before reveal raises `ValueError`. After `solution`, the outcome is still `None`. `self_mark(True)` gives outcome `correct` with `solution_viewed == 1`.
  - `test_shared_solution_marks_siblings_seen`: revealing on `1.1.1(a)` gives `db.book_statuses("demo/01/")["demo/01/1.1.1(b)"] == "seen"`
  - `test_solution_images_carry_attempt`: the solution event for `1.1.2` contains `/api/books/demo/01/img/fig.png?attempt=`
  - `test_flagged_book_problem_listed_in_reports`: flag with a reason, and `db.book_reports()` has that ref and reason (stub the flag model reply)
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_book_tutor.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in `app/tutor.py`. Keep the changes to `hint` and `solution` as early branches, so the paths for generated and imported problems are unchanged.
- [ ] **Step 4: Run** the tests, then the whole suite. Expected: PASS. `tests/test_leak.py` and `tests/test_verify.py` must be unchanged and still pass.
- [ ] **Step 5: Commit** "Serve book hints verbatim and support self-marked book problems".

### Task 4: API routes

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_book_api.py` (FastAPI `TestClient`; `httpx` is already installed)

**Interfaces:**
- Consumes: Tasks 1–3
- Produces:
  - `GET /api/books` gives `books.list_books()`
  - `GET /api/books/{book}/{chapter}` gives `{"chapter": public_chapter, "statuses": {ref: status}, "summary": {"total", "solved", "unaided", "seen"}}`. Statuses are keyed by the short ref (`"7.5"`), and "solved" includes "unaided". It returns 404 on `LookupError` and 500 with `{"detail": BookError message}` on `BookError`.
  - `POST /api/books/{book}/{chapter}/problems/{ref}/open` gives the attempt state (404 if unknown)
  - `POST /api/attempts/{id}/retry` gives the state
  - `POST /api/attempts/{id}/self`, body `{"got_it": bool}`, gives the state (409 on `ValueError`)
  - `GET /api/books/{book}/{chapter}/img/{name}` serves the file. If the name is in `secret_images`, it requires `?attempt=N` where attempt N belongs to a problem of this chapter and has `solution_viewed = 1` or outcome `correct`; otherwise 404. A bad name or slug gives 404.

- [ ] **Step 1: Write failing tests:**
  - `test_chapter_payload_has_no_secrets` (Review Focus 2): the response body contains none of the fixture's solutions, answers or hints
  - `test_open_ref_with_parentheses`: `POST .../problems/1.1.1(b)/open` (URL-encoded) gives 200, and `problem.book_ref` ends in `1.1.1(b)`
  - `test_path_traversal_404s` (Review Focus 1): `img/..%2F..%2Fapp%2Fdb.py`, `img/../chapter.yaml`, book `..`, and an unknown chapter all give 404
  - `test_secret_image_needs_unlocked_attempt`: `fig.png` without attempt gives 404; with an open attempt, 404; after `/solution` on that attempt, 200
  - `test_self_endpoint_conflict_before_reveal`: gives 409
  - `test_statuses_and_summary`: after solving `1.1` unaided, `statuses["1.1"] == "unaided"`, `summary.solved == 1`, `summary.unaided == 1`
  - `test_broken_content_reports_file`: a copied broken book gives a 500 whose detail names the file
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_book_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the routes** in `app/main.py`, matching the existing route style (pydantic body models, `sse` only for streaming routes).
- [ ] **Step 4: Run** the tests, then the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** "Add book routes".

### Task 5: Book view in the frontend

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/app.css`

**Interfaces:**
- Consumes: Task 4 routes; existing `showAttempt`, `refresh`, `onCheck`, `onHint`, `onSolution`, `render`
- Produces: the routes `#/book` (book and chapter list), `#/book/{book}/{chapter}` (chapter page) and `#/book/{book}/{chapter}/{ref}` (chapter page with that problem open)

Decisions the implementer can't make alone:
- **Nav:** add "Book" between Practice and Progress.
- **Reuse, don't copy:** `#problem`, `#work`, `#thread` and `#side` are moved (`appendChild`) into a `#book-tutor` container when a book problem opens, and moved back when Practice is shown, so every existing handler keeps working. `showAttempt(s, fresh, {keepHash: true})` must not rewrite the hash to `#/attempt/...` for book problems.
- **Chapter page:**
  - A section table of contents, then blocks in order. Prose goes through `render()`. Boxes use the existing panel styles, with a label (Important / Concept / Warning / Sidenote / Extra!). ★ sections are marked "Optional".
  - Each problem is a card with its ref, preamble + statement, a status mark (none / started / solution seen / solved / solved unaided), and a "Work on this" button. On a wide screen, the tutor sits in a sticky column beside the text, reusing the `.practice-grid` layout; on a narrow one, it opens below the card.
  - In-text cards also have a collapsed "Show the book's solution" button. It opens the problem and runs `onSolution(true)`.
  - A summary line at the top: "N of M solved, K without help, J solutions seen".
- **Tutor differences when `problem.source === "book"`:**
  - Hide "Another like this".
  - Hint posts from `meta.book` are titled "Hint from the book".
  - Ladder rungs up to `book_hints` are labelled "Book hint" instead of Nudge/Tool/Next step.
  - Badge and verify text: `from_book` gives "Answer from the book" / "Marking uses the book's answer."; `tutor_solution` gives "Solution by the tutor, not the book" / "The book has no solution for this one, so the tutor wrote and checked it."
  - For `self`: the answer box is hidden, and Check becomes "Show the book's answer to compare". After the reveal, two buttons appear: "Got it" and "Didn't get it" (`POST /self`).
  - A finished attempt shows "Try again" (`POST /retry`).
  - After any action, the card's status mark and the summary update without a full re-render. Refetch the chapter payload and patch the marks.

- [ ] **Step 1: Implement the markup, view code and styles** as above, using existing CSS tokens and components only.
- [ ] **Step 2: Verify in the browser** with the fixture book: `THEMATHS_BOOKS=tests/fixtures/books THEMATHS_DATA=/tmp/tm-book ./run.sh`. Use the `run` skill or Playwright, and check each of these, saving a screenshot of each to the scratchpad:
  - the chapter renders with maths and the box
  - an in-text solution is hidden until revealed
  - hint 1 on `1.1` is "Hint from the book"
  - Check on `1.1` with `7` is correct, and the card mark updates
  - `1.1.2` reveals, then "Got it" marks it solved
  - revealing `1.1.1(a)` marks `(b)` as solution seen
  - `fig.png` shows inside the revealed solution
  - Practice still works afterwards (generate or open a problem from History)
  - narrow width (390 px) has no horizontal scroll
- [ ] **Step 3: Run** the whole suite. Expected: PASS.
- [ ] **Step 4: Commit** "Add the Book view".

### Task 6: Transcription method and the real-content test

**Files:**
- Create: `tests/test_book_content.py`, `books/intermediate-algebra/book.yaml`, `books/intermediate-algebra/07/chapter.yaml` (git-ignored except the test)
- Scratch only (never in the repo): `<scratchpad>/scripts/slice.py`, `<scratchpad>/scripts/crop.py`, the venv with PyMuPDF and Pillow

**Interfaces:**
- Produces: the procedure Tasks 7–9 follow, and the test they must pass

**Transcription procedure (used by Tasks 7–9):**
1. Source: `/Users/theredranger/Downloads/BOOKS/Intermediate Algebra/Chapter 7/<file>.png`. Read it in slices 1000 px wide and 1600 px tall, with a 100 px overlap, made by `slice.py <png> <outdir>`. Read every slice; don't skim.
2. Write the section YAML in the Content format above. Keep the text verbatim and the LaTeX faithful to what's displayed. Synthetic-division tables become LaTeX `array`s. Interface text is dropped.
3. Diagrams and graphs are cropped (`crop.py <png> x0 y0 x1 y1 <out>`) into `books/intermediate-algebra/07/img/`, named `<ref-or-section>-<n>.png`. If a screenshot diagram is blurry, render the matching page from the scan (PDF page = book page + 20; Chapter 7 is PDF 213–249) at 200 dpi and crop that instead.
4. Split a multi-part solution at its (a)/(b)/(c) labels, keeping each piece verbatim. Use `covers` only when it can't be split.
5. Answer: derived from the book's solution, in a form `verify.check` accepts. Graphs and "explain why" questions are `self`; "prove/show" questions are `proof`.
6. Cross-check each computational answer with SymPy in the scratch venv. Any disagreement with the book goes in the task report for the student. Never change the book's answer silently.
7. Tutor hints: write enough so every problem has 3 rungs after the book's hints. Follow the `prompts/hint.md` rules for levels: the nudge names no method, the tool names the idea, and the next step gives one instruction. Don't introduce values the student hasn't reached.
8. A missing solution in the screenshots: write one, set `solution_by: tutor`, and list it in the task report.

- [ ] **Step 1: Write `tests/test_book_content.py`.** It is skipped when `books/intermediate-algebra/07/chapter.yaml` is missing, and parametrised over every problem in the chapter:
  - `test_chapter_loads`: `load_chapter` raises nothing
  - `test_answer_marks_itself_correct`: for non-proof, non-self problems, `verify.check(p["answer"], row).result == "correct"`, where `row` is `problem_row`
  - `test_three_rungs`: `len(ladder(p)[0]) == 3`
  - `test_images_exist`: every `![](img/x)` resolves via `image_path`
  - `test_no_interface_text`: no "Hide Solution", "Reset", "Your Submission", "You may type", "Preview: Solution" anywhere in the chapter
- [ ] **Step 2: Write the two scratch scripts**, `book.yaml`, and `chapter.yaml` listing the nine section files. Run the test. Expected: FAIL (section files missing).
- [ ] **Step 3: Commit** only the test, "Check transcribed book content".

### Task 7: Transcribe sections 7.1–7.3

**Files:** `books/intermediate-algebra/07/7-1.yaml`, `7-2.yaml`, `7-3.yaml` and their images (git-ignored)

- [ ] **Step 1:** Follow the Task 6 procedure for each of the three screenshots.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_book_content.py -v -k "7.1 or 7.2 or 7.3"`. Expected: PASS for these refs.
- [ ] **Step 3: Spot-check rendering.** Open `#/book/intermediate-algebra/07` and compare each section side by side with its screenshot. Fix any maths that doesn't render.
- [ ] **Step 4: Report** the SymPy disagreements, tutor-written solutions, and anything ambiguous. Nothing to commit.

### Task 8: Transcribe sections 7.4–7.7

Same as Task 7, for `7-4.yaml` to `7-7.yaml`.

### Task 9: Transcribe the Review and Challenge problems

Same as Task 7, for `review.yaml` and `challenge.yaml`. After this task, run the whole content test with no `-k`. Expected: every problem passes and none is skipped.

### Task 10: End-to-end check and status notes

**Files:**
- Modify: `STATUS.md` (a "Book reader" section: what's built, how to add a chapter, where reports are), `README.md` (one line under Use: the Book tab)

- [ ] **Step 1:** Run the real app on a throwaway DB (`THEMATHS_DATA=/tmp/tm-book ./run.sh`) with qwen3:14b. For three Chapter 7 problems (one in-text, one exercise, one challenge), try a hint with typed working, a wrong answer (error analysis runs), and Why?. Note the timings and any leak-guard rewrites in `STATUS.md`.
- [ ] **Step 2:** Confirm `git status` shows nothing under `books/`, and that `git log -p` on the branch contains no book text. Grep the diff for a distinctive phrase from 7.1.
- [ ] **Step 3:** Run the whole suite. Expected: PASS.
- [ ] **Step 4: Commit** "Document the book reader".
