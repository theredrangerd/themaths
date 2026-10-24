# Book reader: AoPS Intermediate Algebra, Chapter 7

## Intent

The student works through AoPS textbooks and wants the site to *be* the textbook: the chapter laid out cleanly with proper maths, and the existing tutor panel on hand for any problem. Chapter 7 of *Intermediate Algebra* ("Polynomial Roots Part I") is the first chapter and the template. If it works well, the rest of the book follows, and possibly other AoPS books (Precalculus, Calculus).

The book teaches by asking problems *before* explaining. Each section opens with problems, then solves them in the text. Reading straight down a web page would show every solution before the student tries, so hiding those solutions is the central design point.

## Goals

1. A **Book** section of the site where Chapter 7 reads like the book: section text, Important/Concept/Warning boxes, Extra! boxes, diagrams, in-text problems, exercises, review problems and challenge problems.
2. **Text is verbatim.** Wording, problems and solutions are the book's, word for word. Only the formatting changes. Nothing is simplified or paraphrased.
3. **In-text problems hide their solutions.** Each in-text problem (e.g. Problem 7.5) is shown with its statement and the tutor. The book's solution stays collapsed until the student reveals it or solves the problem. Explanatory text *between* problems is always visible.
4. **Every problem has the tutor panel**: working box, Check answer, Hint, Why?, Show solution, "This problem seems wrong". It's the same panel and behaviour as Practice.
5. **The hint ladder starts with the book's hints.** The book's hints for that problem come first, verbatim. Pre-written tutor hints fill any remaining rungs. The last rung is always the book's solution.
6. **The local tutor sees the book's solution** as its reference when hinting, analysing errors, explaining or reviewing proofs, so it steers toward the book's method. The existing leak guards still apply.
7. **Book problems count in Progress** like any other problem. The Book view also shows each problem's status (not started, solved, solved with hints, solution seen).
8. **The chapter is a template.** Adding another chapter or book means adding content, not code.

## Non-goals

- A "come back to this" or revisit queue. The student wants it eventually. Recording `solution_viewed` (already done) keeps the data for it.
- A graphing tool. Graphing exercises are self-marked against the book's graph.
- Free-form chat about the reading ("I don't get this paragraph").
- Chapters other than 7 in this round.
- Importing chapters with the local model. Transcription is always done online with Claude.

## Anti-goals (failure even if it works)

- Reworded or "improved" book text.
- Any way to see a solution before choosing to: in-text solutions rendered open, answers in page source the browser shows, the tutor stating the answer.
- Book content committed to git.
- A different visual style from the existing site.
- Transcription errors in problems or answers. A wrong answer key is worse than none.

## Source material

- **Primary:** full-page screenshots of the AoPS online book, one PNG per section plus Review Problems and Challenge Problems:
  `/Users/theredranger/Downloads/BOOKS/Intermediate Algebra/Chapter 7/` (9 files, 1000 px wide, up to 13,000 px tall).
  They contain the section text, in-text problems with their solutions, exercises with "Hint:" lines and opened solutions, and typeset maths.
  The online book's interface (notes boxes, "Hide Solution"/"Reset" buttons, "Your Submission", comment icons) is not content and is dropped.
- **Secondary:** a scanned PDF of the printed book, `/Users/theredranger/Downloads/AOPS/Intermediate Algebra by The Art of Problem Solving AA.pdf`.
  It has no text layer. Chapter 7 is PDF pages 213–249 (book page + 20). Use it for diagrams that are clearer in print, the back-of-book Hints section if the screenshots lack a hint, and checking anything ambiguous in a screenshot. Render pages with PyMuPDF in a scratch venv; it is not a project dependency.
- About 77 exercise answer boxes in Chapter 7. Every one sampled showed its solution. **If any solution is missing**, Claude writes one, checks it, and labels it in the UI as "Solution by the tutor, not the book". This applies to Chapter 7 only. For later chapters, list the gaps and ask the student before writing any.

## Content: where it lives and what it holds

- Content lives under `books/` at the repo root, which is **git-ignored** (add to `.gitignore`), e.g. `books/intermediate-algebra/07/`. Diagram images are cropped from the screenshots or scan and stored alongside.
- Per chapter: title, number, book name, and the tags book problems get for Progress (Chapter 7 → AMC 12 / Algebra / polynomials; the builder picks the closest existing taxonomy entry).
- Per section: number and title, an optional ★ (supplementary) marker, and an ordered list of blocks: prose, boxed callouts (Important / Concept / Warning / Sidenote / Extra!), images, and problems.
- Per problem:
  - book reference (e.g. `7.5`, `7.1.1`, `7.23(b)`) and kind (in-text, exercise, review, challenge)
  - statement, verbatim
  - book hints, verbatim, in order (possibly none)
  - book solution, verbatim; or Claude's, with a flag saying so
  - answer type and a canonical answer for the existing verifier, or `proof`, or `self` (see below)
  - tutor hints prepared by Claude to fill rungs the book's hints don't cover
- **Each lettered part is its own problem**, with its own answer, hints and progress entry. A shared preamble ("Find all roots of each of the following polynomials:") is shown with every part.
- Answers are derived from the book's solution, not solved independently. Where an answer is computational, Claude checks it with SymPy while transcribing, and any disagreement with the book is raised with the student, not silently resolved.
- The file format is the builder's call. It must hold LaTeX without escaping trouble, since that broke JSON in the existing pipeline; YAML block scalars or Markdown with fenced problem blocks both work. Solutions must never reach the browser until revealed (goal 3): serve them through the existing attempt/solution routes, not inside the page payload.

## Behaviour

### Reading

- New top-nav item **Book**. It shows a list of books and chapters (only Chapter 7 for now), then a chapter page with its sections in order and a section table of contents.
- Problems render inline in reading order. Each shows its book reference, statement, and a status mark. Selecting one opens the tutor panel for it, beside the text on wide screens. It reuses the Practice working area and actions.
- An in-text problem's solution sits below it as a collapsed "Show the book's solution" control. Revealing it is the same action as Show solution in the tutor. It records `solution_viewed` and ends the attempt as `gave_up` if unsolved, as Practice does now. Solving the problem also unlocks it.
- Revealing is always allowed. Nothing is locked, and the student can always move on.

### Problems in the tutor

- Each book problem becomes a `problems` row, `source = 'book'`, created the first time it's opened, with a stable reference (e.g. a new `book_ref` column) so reopening resumes the same problem and its attempts. Statement, answer, hints and solution come from the content. Verification status is shown as from the book.
- Hint ladder (levels 1–3): book hints first, shown verbatim and labelled as the book's. Then tutor hints, tailored to the student's working as now. If the book has three or more hints, the first three fill the ladder. Level 4 is the book's solution.
- Answer types:
  - existing verifier types (integer, expression, set, interval, etc.) work as now
  - `proof`: the student writes an argument and the existing proof review runs against the book's solution
  - `self` (graphs, "explain" questions with no single answer): the student reveals the book's solution and marks themselves "Got it" or "Didn't get it". These count as correct or `gave_up` in Progress.
- Leak guards (`leak.find`, `leak.ahead`) apply to book problems unchanged.
- "This problem seems wrong" also serves as the way to report a transcription mistake.

### Progress

- Book problems appear in the existing Progress and History views under their tags.
- The chapter page shows a small summary: solved, solved unaided, solutions seen.

## Constraints

- Offline at runtime. No new runtime dependencies beyond what `setup.sh` already installs. Fonts, KaTeX and so on are already vendored.
- Same stack and look: FastAPI, vanilla JS, `static/app.css` styles and patterns, KaTeX via `static/render.js`.
- Images are served locally from `books/` through the app.
- Tests in `tests/` for the new pieces: content loading, the hint-ladder order, solution not exposed before reveal, and every Chapter 7 answer parsing under the verifier.

## Decided by the student

- Text verbatim (yes). Proof problems reviewed, not marked (yes). Local tutor uses the book's solution (yes). Reveal any time, recorded (yes). Chapter 7 as template for the rest of the book (yes). Missing solutions filled by Claude for this chapter only. Graphing exercises self-marked (yes). Lettered parts are separate problems (yes). **A solution covering several parts is split at its (a)/(b)/(c) labels**, each piece verbatim and shown only with its own part. If a solution can't be split, revealing it on any part marks every part it covers as "solution seen". Only Chapter 7 images copied; book content git-ignored (yes). Existing look (yes).

## Claude's calls (check these)

- Screenshots as the primary source, scan as secondary.
- Book problems tagged per chapter onto the existing taxonomy so they show up in Progress.
- Book hints shown verbatim even when the student has typed working. Only tutor hints are tailored.
- `self` answer type for graphs and open questions.
- Book problems are created in the database the first time they're opened, not all imported up front.
- **Book hints skip the leak guards.** AoPS wrote them to stop short of the answer, and they often name exactly the values `leak.ahead` would catch, so rewriting them would break "verbatim". Tutor hints and all model replies stay guarded. Needs a test.
- **Content files are the source of truth.** A book problem's row is refreshed from the content each time it's opened (keyed by `book_ref`, compared by a content hash). On a book problem, "This problem seems wrong" records a report for Claude to fix in the next online session. It does not exclude the problem from Progress for good. When the content changes, the flag is cleared.
- **Reopening a problem resumes its latest attempt.** A finished attempt is shown as finished, with a "Try again" button that starts a new attempt. A problem's status mark shows its best outcome.
- **`self` problems:** revealing the solution doesn't end the attempt. "Got it" finishes it as `correct` (with `solution_viewed = 1`, as the comparison required seeing it). "Didn't get it" finishes it as `gave_up`. These count as solved in Progress and the chapter summary.
- **Images inside solutions are secret too.** They are served only through the solution route after reveal, never by a public URL. Images in statements and prose are public.

## Left to the builder

- Content file format and loader.
- Exact layout of the chapter page and where the tutor panel sits on narrow screens.
- How the summary and status marks look, within the existing style.
