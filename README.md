# themaths

An offline maths tutor for IB AA HL and AMC 12 practice, powered by local models through Ollama. It holds back answers by default, guides you up a four-step hint ladder, checks answers with SymPy rather than trusting the model, and keeps a local record of your progress.

See [SPEC.md](SPEC.md) for the full specification.

## Setup (while online)

```sh
./setup.sh                 # creates .venv and installs Python packages
ollama pull qwen3:8b       # tutor model (or a larger one; see below)
ollama pull gemma4         # screenshot model
./run.sh --check           # confirms everything needed offline is present
```

KaTeX, marked and DOMPurify are vendored in `static/vendor/`, so nothing is fetched at runtime.

## Use

```sh
./run.sh
```

This starts the server on `http://127.0.0.1:8737` and opens it in your browser. Ollama must be running.

- **Practice:** ask for a problem in your own words, or set track, topic and difficulty. Paste or drop a screenshot of a printed problem to practise that instead.
- **Book:** read a transcribed book chapter in the Book tab and work its problems with the same hints, checking and "Why?" (book hints come first).
- **Your working:** type where you've got to (LaTeX or plain text). Hints are tailored to it.
- **Check answer:** marked by SymPy against the stored answer. If you're wrong, the tutor points to where your working went wrong without giving the answer.
- **Hint:** nudge, then tool, then next step, then the full solution.
- **Why?:** a concept explanation that uses the problem as its example.
- **This problem seems wrong:** flags it; flagged problems are excluded from your statistics.
- **Progress:** solve rate and hint use per topic, and problems solved per week.

Your data lives in `data/themaths.db` (git-ignored). Export it from Settings.

## How answers are verified

Generating a problem takes two model calls:

1. The model writes the problem, its own answer, and Python code that computes the answer (brute force where possible). The code runs in a restricted subprocess.
   - If the two agree, the problem is marked **Answer verified**.
   - If they disagree, the model is asked to reconcile them. A problem that stays disputed is discarded rather than shown.
   - If the code can't run, the problem is shown as **Unverified answer**.
2. The model writes the solution, hints and, for AMC, four plausible wrong options aimed at the settled answer.

## Models

Choose the tutor and screenshot models in Settings. Small models make mistakes: an 8B model writes weaker problems, and its checking code sometimes misreads the problem. Use the largest model your Mac runs comfortably.

"Let the model think" (in Settings) is more reliable but much slower: with qwen3:8b, a single problem can take several minutes.

## Development

```sh
.venv/bin/python -m pytest       # verifier and sandbox tests
THEMATHS_DATA=/tmp/tm ./run.sh   # use a throwaway database
```

- Prompts: `prompts/`
- Topic list: `taxonomy.yaml`
