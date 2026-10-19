Write one new practice problem for the student.

Student's request: {{request}}
{{constraints}}

Topic list (use these exact names for track, topic and subtopic):
{{taxonomy}}

Difficulty guidance
- IB straightforward: a single routine technique. standard: a typical paper 1 or 2 question. exam-hard: the final, multi-step section B question of a paper. beyond-syllabus: an IB topic, but needing an idea a strong student would not have been taught.
- AMC 1-10: quick, one key idea. 11-20: two ideas combined, or a careful case analysis. 21-25: the hardest AMC 12 problems; a non-obvious insight is required.
- Make the problem genuinely interesting: it should reward insight, not just grinding. Vary the setting and structure; avoid stock phrasings such as "How many positive integers less than 1000...".
- AMC problems will be shown as multiple choice; options are added later, so do NOT write options into the statement. Their answers should be a single number or short exact expression.
- IB problems are free response. Prefer exact answers; if a decimal answer is natural, state the required precision in the problem (e.g. "to three significant figures").

{{related}}
{{avoid}}

The problem must be well posed and have exactly one answer.

Return JSON with these fields:
- statement: the problem in Markdown with LaTeX (inline $...$, display $$...$$). No answer options.
- track: "IB" or "AMC".
- topic, subtopic, difficulty: from the lists above.
- answer_type: one of exact_expression, numeric, integer, set, interval, proof.
- answer: your final answer only, in plain LaTeX without $ signs (e.g. \frac{3}{4}, 2\sqrt{3}, \{-1, 2\}, [0, 2)). For proof, a one-line summary of the claim.
- verification: Python code that computes the answer directly and assigns it to a variable named result. Use sympy (import it explicitly) and the standard library only (math, itertools, fractions, functools, collections). Model the problem statement exactly, and make the code do the work: never assign result a number you worked out yourself. Prefer brute-force enumeration for counting, probability and number theory (use exact Fractions for probabilities); direct symbolic computation for algebra and calculus; coordinates for geometry. For set answers, result is a Python set or sympy FiniteSet; for intervals, a sympy Interval or Union. For proof, numerically spot-check the claim and set result = True.
