The student has supplied the following problem (transcribed from a screenshot). Prepare it for practice. Do not change the problem.

Problem:
{{statement}}

{{constraints}}

Topic list (use these exact names for track, topic and subtopic):
{{taxonomy}}

If the problem lists options (A) to (E), it is multiple choice.

Return JSON with these fields:
- statement: the problem exactly as given above.
- track: "AMC" if it is a multiple-choice contest problem, otherwise "IB".
- topic, subtopic, difficulty: from the lists above.
- answer_type: multiple_choice if it has options; otherwise one of exact_expression, numeric, integer, set, interval, proof.
- answer: your final answer only, in plain LaTeX without $ signs. For multiple_choice, the letter only.
- choices: for multiple_choice, the five option values in order (LaTeX without $ signs); otherwise an empty list.
- verification: Python code that computes the answer directly and assigns it to a variable named result, using sympy and the standard library only. Model the problem exactly and make the code do the work (never assign result a number you worked out yourself); prefer brute force where feasible. For multiple_choice, result is the numeric value of the correct option, not the letter.
