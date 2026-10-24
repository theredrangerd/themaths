// themaths frontend: hash-routed single page, no build step.
(function () {
  const $ = (id) => document.getElementById(id);
  const { render, renderInline, renderChoices, escapeHtml } = window.MathRender;

  const HINT_TITLES = { 1: "Hint 1: a nudge", 2: "Hint 2: the tool", 3: "Hint 3: the next step", 4: "Solution" };
  const SUGGESTIONS = [
    { text: "A hard AMC 12 counting problem", track: "AMC", difficulty: "21-25" },
    { text: "IB HL integration by parts, exam style", track: "IB" },
    { text: "Something on roots of unity", track: "" },
    { text: "A mid-contest AMC 12 number theory problem", track: "AMC", difficulty: "11-20" },
    { text: "IB HL proof by induction", track: "IB" },
  ];

  const state = {
    taxonomy: null,
    current: null,       // attempt state from the server
    controller: null,    // AbortController for the active stream
    busySince: 0,
    busyTimer: null,
    confirmSolution: false,
    book: null,          // the chapter on show: { slug, chapter, data }
    books: null,         // the book list, for titles
    session: { problems: 0, solved: 0, hints: 0 },
  };

  // API -------------------------------------------------------------------

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return res.json();
  }

  // POST a request and consume its Server-Sent Events.
  async function stream(url, body, onEvent) {
    state.controller = new AbortController();
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal: state.controller.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        if (chunk.startsWith("data: ")) onEvent(JSON.parse(chunk.slice(6)));
      }
    }
  }

  // Busy state ------------------------------------------------------------

  function setBusy(text) {
    $("busy").hidden = false;
    $("busy-text").textContent = text;
    if (!state.busyTimer) {
      state.busySince = Date.now();
      state.busyTimer = setInterval(tickBusy, 1000);
      tickBusy();
    }
    document.body.classList.add("is-busy");
    syncButtons();
  }

  function tickBusy() {
    const s = Math.round((Date.now() - state.busySince) / 1000);
    $("busy-time").textContent = s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
  }

  function clearBusy() {
    $("busy").hidden = true;
    clearInterval(state.busyTimer);
    state.busyTimer = null;
    state.controller = null;
    document.body.classList.remove("is-busy");
    syncButtons();
  }

  const isBusy = () => !!state.busyTimer;

  // Generic handler for a streamed action: status/thinking go to the busy line,
  // deltas into a post, done/error resolve.
  async function runStream(url, body, { busyText, post, onDone }) {
    setBusy(busyText);
    let stage = busyText;
    let text = "";
    let errored = false;
    try {
      await stream(url, body, (e) => {
        if (e.type === "status") { stage = e.text; setBusy(e.text); }
        else if (e.type === "thinking") setBusy(`${stage.replace(/…$/, "")}, thinking…`);
        else if (e.type === "writing") setBusy(`${stage.replace(/…$/, "")} (${e.chars.toLocaleString()} characters so far)`);
        else if (e.type === "delta") { text += e.text; post && post.set(text); }
        else if (e.type === "error") { errored = true; showError(e.text, post); }
        else if (e.type === "done") onDone && onDone(e);
      });
    } catch (err) {
      if (err.name === "AbortError") post ? post.set(text || "_Stopped._") : null;
      else { errored = true; showError(err.message, post); }
    } finally {
      clearBusy();
    }
    return !errored;
  }

  function showError(message, post) {
    if (post) { post.el.dataset.kind = "error"; post.title("Something went wrong"); post.set(escapeHtml(message)); }
    else addPost("error", "Something went wrong").set(escapeHtml(message));
  }

  // Thread posts ----------------------------------------------------------

  function addPost(kind, title, sub = "") {
    const el = document.createElement("article");
    el.className = "post";
    el.dataset.kind = kind;
    el.innerHTML = `<header class="post-head"><span class="post-title"></span><span class="post-sub"></span></header>
      <div class="post-body math-prose"><p class="post-waiting">Writing…</p></div>`;
    el.querySelector(".post-title").textContent = title;
    el.querySelector(".post-sub").textContent = sub;
    $("thread").appendChild(el);
    const body = el.querySelector(".post-body");
    let pending = null;
    const post = {
      el,
      set(md) {
        pending = md;
        requestAnimationFrame(() => {
          if (pending === null) return;
          body.innerHTML = render(pending);
          pending = null;
        });
      },
      title(t) { el.querySelector(".post-title").textContent = t; },
    };
    if (kind !== "error") el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
    return post;
  }

  function postForEvent(ev) {
    switch (ev.kind) {
      case "hint": return addPost("hint", ev.meta && ev.meta.book ? "Hint from the book" : HINT_TITLES[ev.level] || "Hint");
      case "solution": return addPost("solution", solutionTitle());
      case "check": {
        const ok = ev.meta.result === "correct";
        return addPost(ok ? "correct" : "incorrect", ok ? "Correct" : "Not quite", `You answered ${ev.content}`);
      }
      case "analysis":
        return ev.meta.proof
          ? addPost("analysis", "Review of your proof", "The tutor's reading, not machine-checked")
          : addPost("analysis", "Where your working went wrong", "The tutor's reading, not machine-checked");
      case "explain": return addPost("explain", "Why it works", ev.meta.question || "");
      case "flag": return addPost("flag", "Review of your flag", ev.meta.reason || "");
      default: return addPost(ev.kind, ev.kind);
    }
  }

  const isBook = (s) => !!s && s.problem.source === "book";

  function solutionTitle() {
    const p = state.current && state.current.problem;
    return p && p.verification_status === "tutor_solution" ? "Solution by the tutor, not the book" : "Solution";
  }

  function checkBody(ev) {
    const ok = ev.meta.result === "correct";
    const note = ev.meta.note ? `\n\n${ev.meta.note}` : "";
    return ok ? `Your answer matches.${note}` : `Your answer doesn't match.${note}`;
  }

  // Practice view ---------------------------------------------------------

  function fillSelect(sel, values, labels) {
    const keep = sel.value;
    sel.innerHTML = `<option value="">Any</option>` + values.map((v, i) => `<option value="${escapeHtml(v)}">${escapeHtml(labels ? labels[i] : v)}</option>`).join("");
    if (values.includes(keep)) sel.value = keep;
  }

  function syncOptions() {
    const tax = state.taxonomy;
    const track = $("opt-track").value;
    if (!track) {
      $("opt-topic").disabled = $("opt-difficulty").disabled = true;
      fillSelect($("opt-topic"), []);
      fillSelect($("opt-difficulty"), []);
      return;
    }
    $("opt-topic").disabled = $("opt-difficulty").disabled = false;
    fillSelect($("opt-topic"), Object.keys(tax[track].topics));
    fillSelect($("opt-difficulty"), tax[track].difficulty);
  }

  async function generate(body) {
    $("screenshot-panel").hidden = true;
    const ok = await runStream("/api/problems/generate", body, {
      busyText: body.related_attempt ? "Writing a related problem…" : "Writing a problem…",
      onDone: (e) => { state.session.problems++; showAttempt(e.state, true); },
    });
    return ok;
  }

  function showAttempt(s, fresh = false, { keepHash = false } = {}) {
    state.current = s;
    state.confirmSolution = false;
    const p = s.problem;
    const a = s.attempt;
    const tax = state.taxonomy[p.track];

    $("problem-empty").hidden = true;
    $("problem-body").hidden = false;
    $("work").hidden = false;
    $("side").hidden = false;

    const tags = [`<span class="tag tag-track">${escapeHtml(tax ? tax.name : p.track)}</span>`, `<span class="tag">${escapeHtml(p.topic)}</span>`];
    if (p.subtopic) tags.push(`<span class="tag">${escapeHtml(p.subtopic)}</span>`);
    if (p.difficulty) tags.push(`<span class="tag">${escapeHtml(p.track === "AMC" ? `Problems ${p.difficulty.replace("-", "–")}` : p.difficulty)}</span>`);
    if (p.source === "screenshot") tags.push(`<span class="tag">From a screenshot</span>`);
    if (p.source === "book") tags.push(`<span class="tag">Book problem ${escapeHtml(shortRef(p.book_ref))}</span>`);
    $("problem-tags").innerHTML = tags.join("");

    const badge = $("problem-badge");
    badge.dataset.status = p.verification_status;
    badge.textContent = {
      verified: "Answer verified", book: "Answer from your book", computed: "Answer computed", unverified: "Unverified answer", not_applicable: "Proof",
      from_book: "Answer from the book", tutor_solution: "Solution by the tutor, not the book",
    }[p.verification_status];

    $("problem-statement").innerHTML = render(p.statement);
    $("problem-choices").innerHTML = renderChoices(p.choices);

    $("working").value = a.working || "";
    $("answer").value = a.final_answer || "";
    $("answer").placeholder = p.answer_type === "multiple_choice" ? "A letter, A to E" : p.answer_type === "proof" ? "Write your proof in the working box" : "e.g. 3/4, 2\\sqrt{3}";
    $("answer").disabled = p.answer_type === "proof";
    $("answer").closest(".answer-field").hidden = p.answer_type === "self";
    $("act-check").textContent = p.answer_type === "proof" ? "Submit proof for review" : p.answer_type === "self" ? "Show the book's answer to compare" : "Check answer";
    updateWorkingPreview();
    updateAnswerPreview();

    $("verify-text").textContent = {
      verified: "The tutor's answer was confirmed by an independent computation, so marking is reliable.",
      computed: "The answer was computed directly by code, which disagreed with the tutor's first attempt. Marking uses the computed value. If the problem itself seems flawed, flag it.",
      book: "Marking uses the answer you entered from your book.",
      unverified: "The tutor's answer couldn't be confirmed by computation. Answers are still marked against it, so if a verdict looks wrong, flag the problem.",
      not_applicable: "Proofs can't be checked by computation. The tutor reviews your proof instead.",
      from_book: "Marking uses the book's answer.",
      tutor_solution: "The book has no solution for this one, so the tutor wrote and checked it.",
    }[p.verification_status];
    delete $("verify-text").dataset.noted;
    if (p.verification_note) {
      $("verify-text").textContent += ` ${p.verification_note}`;
      $("verify-text").dataset.noted = "1";
    }

    $("thread").innerHTML = "";
    for (const ev of s.events) {
      const post = postForEvent(ev);
      post.set(ev.kind === "check" ? checkBody(ev) : ev.content);
    }
    syncLadder();
    syncButtons();
    $("explain-form").hidden = $("flag-form").hidden = true;

    const target = `#/attempt/${s.attempt_id}`;
    if (!keepHash && location.hash !== target) history.replaceState(null, "", target);
    if (fresh) {
      $("problem").scrollIntoView({ block: "start", behavior: "auto" });
      $("working").focus({ preventScroll: true });
    }
    renderSession();
    if (isBook(s)) syncBookMarks();
  }

  function syncLadder() {
    const level = state.current ? state.current.attempt.hint_level : 0;
    const bookHints = isBook(state.current) ? state.current.problem.book_hints || 0 : 0;
    for (const li of $("ladder").children) {
      const rung = Number(li.dataset.level);
      li.classList.toggle("used", rung <= level);
      const name = li.querySelector(".rung-name");
      const desc = li.querySelector(".rung-desc");
      if (!li.dataset.name) { li.dataset.name = name.textContent; li.dataset.desc = desc.textContent; }
      const fromBook = rung < 4 && rung <= bookHints;
      name.textContent = fromBook ? "Book hint" : li.dataset.name;
      desc.textContent = fromBook ? "from the book" : li.dataset.desc;
    }
    const next = Math.min(level + 1, 4);
    const btn = $("act-hint");
    btn.textContent = next === 4 ? "Show solution" : `Hint ${next}`;
  }

  function syncButtons() {
    const busy = isBusy();
    const has = !!state.current;
    for (const id of ["act-check", "act-hint", "act-explain", "act-another", "act-solution", "act-flag", "act-got", "act-missed", "act-retry"]) $(id).disabled = busy || !has;
    const book = isBook(state.current);
    const self = book && state.current.problem.answer_type === "self";
    const a = has ? state.current.attempt : {};
    $("act-another").hidden = book;
    $("act-check").hidden = self && !!a.solution_viewed;
    $("act-got").hidden = $("act-missed").hidden = !(self && a.solution_viewed && !a.finished_at);
    $("act-retry").hidden = !(book && a.finished_at);
    if (book && a.solution_viewed) $("act-hint").disabled = true;
    $("ask-submit").disabled = busy;
    $("screenshot-btn").disabled = busy;
    if (has && state.current.problem.flagged) $("act-flag").disabled = true;
    if (has && state.current.attempt.solution_viewed) $("act-solution").disabled = true;
    if (!state.confirmSolution) $("act-solution").textContent = "Show solution";
  }

  function renderSession() {
    const s = state.session;
    $("session-stats").innerHTML = `
      <dt>Problems</dt><dd>${s.problems}</dd>
      <dt>Solved</dt><dd>${s.solved}</dd>
      <dt>Hints taken</dt><dd>${s.hints}</dd>`;
  }

  let previewTimer = null;
  function updateWorkingPreview() {
    const v = $("working").value;
    $("working-preview").hidden = !v.trim();
    $("working-preview").innerHTML = render(v);
  }
  function updateAnswerPreview() {
    $("answer-preview").innerHTML = state.current && state.current.problem.answer_type === "multiple_choice" ? "" : renderInline($("answer").value);
  }

  let saveTimer = null;
  function scheduleSave() {
    clearTimeout(saveTimer);
    if (!state.current) return;
    const id = state.current.attempt_id;
    saveTimer = setTimeout(() => {
      api("PUT", `/api/attempts/${id}/working`, { working: $("working").value, answer: $("answer").value }).catch(() => {});
    }, 800);
  }

  const working = () => ({ working: $("working").value, answer: $("answer").value });

  async function onCheck() {
    const id = state.current.attempt_id;
    if (state.current.problem.answer_type === "self") return onSolution(true);
    if (state.current.problem.answer_type === "proof") {
      if (!$("working").value.trim()) { $("working").focus(); return; }
      const post = addPost("analysis", "Review of your proof", "The tutor's reading, not machine-checked");
      await runStream(`/api/attempts/${id}/analyse`, working(), {
        busyText: "Reviewing your proof…",
        post,
        onDone: (e) => { post.set(e.text); refresh(e.state); },
      });
      return;
    }
    let res;
    try { res = await api("POST", `/api/attempts/${id}/check`, working()); }
    catch (err) { showError(err.message); return; }

    if (res.result === "unparseable") {
      addPost("incorrect", "Couldn't read that answer").set(escapeHtml(res.note));
      $("answer").focus();
      return;
    }
    const wasOpen = !state.current.attempt.finished_at;
    refresh(res.state);
    const ev = res.state.events[res.state.events.length - 1];
    postForEvent(ev).set(checkBody(ev));
    if (res.result === "correct") {
      if (wasOpen && !res.state.attempt.solution_viewed) state.session.solved++;
      renderSession();
      return;
    }
    const post = addPost("analysis", "Where your working went wrong", "The tutor's reading, not machine-checked");
    await runStream(`/api/attempts/${id}/analyse`, working(), {
      busyText: "Reading your working…",
      post,
      onDone: (e) => { post.set(e.text); refresh(e.state); },
    });
  }

  // Update stored state without rebuilding the thread.
  function refresh(s) {
    state.current = s;
    syncLadder();
    syncButtons();
    if (s.problem.verification_note && !$("verify-text").dataset.noted) {
      $("verify-text").textContent += ` ${s.problem.verification_note}`;
      $("verify-text").dataset.noted = "1";
    }
    if (isBook(s)) syncBookMarks();
  }

  async function onHint() {
    const level = Math.min(state.current.attempt.hint_level + 1, 4);
    if (level === 4) return onSolution(true);
    const p = state.current.problem;
    const post = addPost("hint", isBook(state.current) && level <= (p.book_hints || 0) ? "Hint from the book" : HINT_TITLES[level]);
    state.session.hints++;
    renderSession();
    await runStream(`/api/attempts/${state.current.attempt_id}/hint`, working(), {
      busyText: "Writing a hint…",
      post,
      onDone: (e) => { post.set(e.text); refresh(e.state); },
    });
  }

  async function onSolution(confirmed = false) {
    if (!confirmed && !state.confirmSolution) {
      state.confirmSolution = true;
      $("act-solution").textContent = "Click again to see the solution";
      setTimeout(() => { state.confirmSolution = false; syncButtons(); }, 4000);
      return;
    }
    state.confirmSolution = false;
    const post = addPost("solution", solutionTitle());
    await runStream(`/api/attempts/${state.current.attempt_id}/solution`, {}, {
      busyText: "Opening the solution…",
      post,
      onDone: (e) => { post.set(e.text); refresh(e.state); },
    });
  }

  // A self-marked book problem: after the reveal, the student says how they did.
  async function onSelf(gotIt) {
    let s;
    try { s = await api("POST", `/api/attempts/${state.current.attempt_id}/self`, { got_it: gotIt }); }
    catch (err) { showError(err.message); return; }
    if (gotIt) { state.session.solved++; renderSession(); }
    refresh(s);
  }

  async function onRetry() {
    let s;
    try { s = await api("POST", `/api/attempts/${state.current.attempt_id}/retry`); }
    catch (err) { showError(err.message); return; }
    showAttempt(s, false, { keepHash: !$("view-book").hidden });
  }

  async function onExplain(ev) {
    ev.preventDefault();
    const question = $("explain-q").value;
    $("explain-form").hidden = true;
    const post = addPost("explain", "Why it works", question);
    await runStream(`/api/attempts/${state.current.attempt_id}/explain`, { ...working(), question }, {
      busyText: "Writing an explanation…",
      post,
      onDone: (e) => { post.set(e.text); refresh(e.state); },
    });
    $("explain-q").value = "";
  }

  async function onFlag(ev) {
    ev.preventDefault();
    const reason = $("flag-reason").value;
    $("flag-form").hidden = true;
    const post = addPost("flag", "Review of your flag", reason);
    await runStream(`/api/attempts/${state.current.attempt_id}/flag`, { reason }, {
      busyText: "Reviewing the problem…",
      post,
      onDone: (e) => { post.set(e.text); refresh(e.state); },
    });
    $("flag-reason").value = "";
  }

  // Screenshots -----------------------------------------------------------

  function readAsBase64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(",")[1]);
      r.onerror = reject;
      r.readAsDataURL(file);
    });
  }

  async function transcribe(file) {
    if (isBusy()) return;
    const image = await readAsBase64(file);
    $("screenshot-panel").hidden = false;
    $("screenshot-text").value = "";
    $("screenshot-preview").innerHTML = "";
    $("import-answer").value = $("import-working").value = "";
    let text = "";
    await runStream("/api/transcribe", { image }, {
      busyText: "Reading the screenshot…",
      post: { set: (t) => { $("screenshot-text").value = t; $("screenshot-preview").innerHTML = render(t); }, el: document.createElement("div"), title() {} },
      onDone: (e) => { text = e.statement; },
    });
    if (text) {
      $("screenshot-text").value = text;
      $("screenshot-preview").innerHTML = render(text);
    }
    $("screenshot-text").focus();
  }

  async function importScreenshot() {
    const statement = $("screenshot-text").value.trim();
    if (!statement) return;
    const body = {
      statement,
      book_answer: $("import-answer").value,
      working: $("import-working").value,
      track: $("opt-track").value || null,
    };
    const ok = await runStream("/api/problems/import", body, {
      busyText: "Preparing hints for this problem…",
      onDone: (e) => { state.session.problems++; showAttempt(e.state, true); },
    });
    if (ok) {
      $("screenshot-panel").hidden = true;
      $("import-answer").value = $("import-working").value = "";
    }
  }

  // Book view -------------------------------------------------------------
  //
  // The chapter is read on the left; the Practice tutor (#problem, #work, #thread, #side,
  // and the busy line) is moved beside it, or below the card on a narrow screen, so every
  // existing handler keeps working. It is moved back when another view is shown.

  const BOX_LABELS = { important: "Important", concept: "Concept", warning: "Warning", sidenote: "Sidenote", extra: "Extra!" };
  const KIND_LABELS = { intext: "Problem", exercise: "Exercise", review: "Review problem", challenge: "Challenge problem" };
  const MARKS = { started: "Started", seen: "Solution seen", solved: "Solved", unaided: "Solved unaided" };
  const MARK_OUTCOME = { started: "open", seen: "gave_up", solved: "correct", unaided: "correct" };
  const TUTOR_PARTS = ["busy", "problem", "work", "thread", "side"];
  const tutorHomes = new Map();
  const narrow = matchMedia("(max-width: 980px)");

  const shortRef = (bookRef) => (bookRef || "").split("/").slice(2).join("/");
  const bookHash = (slug, chapter, ref) =>
    `#/book/${encodeURIComponent(slug)}/${encodeURIComponent(chapter)}${ref ? `/${encodeURIComponent(ref)}` : ""}`;
  const chapterUrl = (slug, chapter) => `/api/books/${encodeURIComponent(slug)}/${encodeURIComponent(chapter)}`;
  const cardFor = (ref) => [...document.querySelectorAll(".book-card")].find((c) => c.dataset.ref === ref);

  function moveTutorIn(ref) {
    const tutor = $("book-tutor");
    for (const id of TUTOR_PARTS) {
      const el = $(id);
      if (!tutorHomes.has(id)) {
        const mark = document.createComment(id);
        el.before(mark);
        tutorHomes.set(id, mark);
      }
      tutor.appendChild(el);
    }
    $("book-tutor-title").textContent = `Working on ${ref}`;
    tutor.hidden = false;
    for (const c of document.querySelectorAll(".book-card")) c.classList.toggle("is-active", c.dataset.ref === ref);
    placeTutor();
  }

  function moveTutorHome() {
    for (const [id, mark] of tutorHomes) mark.replaceWith($(id));
    tutorHomes.clear();
    $("book-tutor").hidden = true;
    $("book-aside").appendChild($("book-tutor"));
    for (const c of document.querySelectorAll(".book-card.is-active")) c.classList.remove("is-active");
  }

  // Wide: a sticky column beside the text. Narrow: straight after the card being worked on.
  function placeTutor() {
    const tutor = $("book-tutor");
    if (tutor.hidden || !isBook(state.current)) return;
    const card = cardFor(shortRef(state.current.problem.book_ref));
    if (narrow.matches && card) card.after(tutor);
    else $("book-aside").appendChild(tutor);
  }

  async function showBookIndex() {
    const body = $("book-index");
    try { state.books = await api("GET", "/api/books"); }
    catch (err) { body.innerHTML = `<p class="empty-page">${escapeHtml(err.message)}</p>`; return; }
    if (!state.books.length) {
      body.innerHTML = `<h1 class="page-title">Book</h1><div class="empty-page">No books yet. Add a book's folder to <code>books/</code> and reload.</div>`;
      return;
    }
    body.innerHTML = `<h1 class="page-title">Book</h1>${state.books.map((b) => `
      <h2 class="section-title">${escapeHtml(b.title)}</h2>
      <ul class="history-list">${b.chapters.map((c) => `<li class="history-item"><a href="${bookHash(b.slug, c.slug)}">
        <span class="history-statement">Chapter ${escapeHtml(String(c.number))}: ${escapeHtml(c.title || "")}</span>
      </a></li>`).join("")}</ul>`).join("")}`;
  }

  function blockHtml(b) {
    if ("prose" in b) return `<div class="math-prose book-prose">${render(b.prose)}</div>`;
    if ("box" in b) {
      return `<aside class="book-box"><h3 class="side-title">${escapeHtml(BOX_LABELS[b.box] || b.box)}</h3>
        <div class="math-prose">${render(b.text)}</div></aside>`;
    }
    const p = b.problem;
    return `<article class="problem book-card" data-ref="${escapeHtml(p.ref)}">
      <header class="problem-head">
        <span class="book-card-ref">${escapeHtml(KIND_LABELS[p.kind] || "Problem")} ${escapeHtml(p.ref)}</span>
        <span class="outcome book-mark"></span>
      </header>
      <div class="math-prose book-card-statement">${render(p.preamble || "")}${render(p.statement)}</div>
      <div class="row-actions">
        <button class="btn btn-secondary" type="button" data-act="open">Work on this</button>
        ${p.kind === "intext" ? `<button class="btn-link" type="button" data-act="reveal">Show the book's solution</button>` : ""}
      </div>
    </article>`;
  }

  function renderChapter() {
    const { slug, chapter, data } = state.book;
    const ch = data.chapter;
    const book = (state.books || []).find((b) => b.slug === slug);
    const heading = (s) => {
      const parts = [s.number, s.title].filter((x) => x !== undefined && x !== null && x !== "").map(String);
      // Review and Challenge sections use one label for both fields; show it once.
      if (parts.length === 2 && parts[0].trim() === parts[1].trim()) parts.pop();
      return parts.map(escapeHtml).join(" ");
    };
    $("book-aside").appendChild($("book-tutor"));
    $("book-text").innerHTML = `
      <p class="book-crumb"><a href="#/book">${escapeHtml(book ? book.title : slug)}</a></p>
      <h1 class="page-title">Chapter ${escapeHtml(String(ch.number))}: ${escapeHtml(ch.title)}</h1>
      <p class="book-summary" id="book-summary"></p>
      <nav class="book-toc" aria-label="Sections">
        <h2 class="side-title">Sections</h2>
        <ol>${ch.sections.map((s, i) => `<li><a href="${bookHash(slug, chapter)}" data-section="${i}">${heading(s)}</a>${s.star ? ` <span class="optional">Optional</span>` : ""}</li>`).join("")}</ol>
      </nav>
      ${ch.sections.map((s, i) => `<section class="book-section" id="book-sec-${i}">
        <h2 class="section-title">${heading(s)}${s.star ? ` <span class="tag">Optional</span>` : ""}</h2>
        ${s.blocks.map(blockHtml).join("")}
      </section>`).join("")}`;
    patchMarks();
  }

  function patchMarks() {
    const { statuses, summary } = state.book.data;
    for (const card of document.querySelectorAll(".book-card")) {
      const st = statuses[card.dataset.ref];
      const mark = card.querySelector(".book-mark");
      mark.textContent = st ? MARKS[st] : "";
      if (st) mark.dataset.outcome = MARK_OUTCOME[st];
      else delete mark.dataset.outcome;
    }
    const seen = summary.seen;
    $("book-summary").textContent =
      `${summary.solved} of ${summary.total} solved, ${summary.unaided} without help, ${seen} solution${seen === 1 ? "" : "s"} seen`;
  }

  // Refetch the chapter's statuses and patch the marks, without re-rendering the text.
  let marksSeq = 0;
  async function syncBookMarks() {
    const b = state.book;
    if (!b || !b.data) return;
    const seq = ++marksSeq;
    let data;
    try { data = await api("GET", chapterUrl(b.slug, b.chapter)); } catch (_) { return; }
    if (seq !== marksSeq || state.book !== b) return;
    b.data.statuses = data.statuses;
    b.data.summary = data.summary;
    patchMarks();
  }

  async function showBook(slug, chapter, ref) {
    const hash = location.hash;
    if (!slug || !chapter) {
      moveTutorHome();
      $("book-chapter").hidden = true;
      $("book-index").hidden = false;
      return showBookIndex();
    }
    $("book-index").hidden = true;
    $("book-chapter").hidden = false;
    if (!state.book || state.book.slug !== slug || state.book.chapter !== chapter) {
      moveTutorHome();
      const b = (state.book = { slug, chapter, data: null });
      $("book-text").innerHTML = `<p class="post-waiting">Loading the chapter…</p>`;
      try {
        if (!state.books) state.books = await api("GET", "/api/books").catch(() => []);
        b.data = await api("GET", chapterUrl(slug, chapter));
      } catch (err) {
        if (state.book === b) {
          state.book = null;
          $("book-text").innerHTML = `<div class="empty-page">${escapeHtml(err.message)} <a href="#/book">All books</a></div>`;
        }
        return;
      }
      if (state.book !== b || location.hash !== hash) return;
      renderChapter();
    } else {
      syncBookMarks();
    }
    if (!ref) { moveTutorHome(); return; }

    const full = `${slug}/${chapter}/${ref}`;
    if (!isBook(state.current) || state.current.problem.book_ref !== full) {
      if (isBusy()) return;
      let s;
      try { s = await api("POST", `${chapterUrl(slug, chapter)}/problems/${encodeURIComponent(ref)}/open`); }
      catch (err) {
        const card = cardFor(ref);
        if (card) card.querySelector(".row-actions").insertAdjacentHTML("beforeend", `<span class="field-help">${escapeHtml(err.message)}</span>`);
        return;
      }
      if (location.hash !== hash) return;
      showAttempt(s, false, { keepHash: true });
      moveTutorIn(ref);
      if (!narrow.matches) $("working").focus({ preventScroll: true });
      return;
    }
    moveTutorIn(ref);
  }

  async function openCard(ref, reveal) {
    if (isBusy() || !state.book) return;
    const { slug, chapter } = state.book;
    const target = bookHash(slug, chapter, ref);
    if (location.hash !== target) history.pushState(null, "", target);
    await showBook(slug, chapter, ref);
    const cur = state.current;
    if (reveal && isBook(cur) && cur.problem.book_ref === `${slug}/${chapter}/${ref}` && !cur.attempt.solution_viewed) onSolution(true);
  }

  function onBookClick(e) {
    const section = e.target.closest("[data-section]");
    if (section) {
      e.preventDefault();
      $(`book-sec-${section.dataset.section}`).scrollIntoView({ block: "start" });
      return;
    }
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const card = btn.closest(".book-card");
    if (card) openCard(card.dataset.ref, btn.dataset.act === "reveal");
  }

  // Progress view ---------------------------------------------------------

  const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
  const one = (x) => (x === null || x === undefined ? "–" : x.toFixed(1));

  async function showProgress() {
    const body = $("progress-body");
    let data;
    try { data = await api("GET", "/api/progress"); }
    catch (err) { body.innerHTML = `<p class="empty-page">${escapeHtml(err.message)}</p>`; return; }
    const t = data.totals;
    if (!t.attempted && !t.flagged) {
      body.innerHTML = `<div class="empty-page">Nothing to show yet. Progress appears here once you've checked an answer or taken a hint. <a href="#/practice">Start practising</a></div>`;
      return;
    }
    const maxWeek = Math.max(1, ...data.weeks.map((w) => w.solved));
    const dayMonth = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
    const weeks = data.weeks.map((w, i) => {
      const start = dayMonth.format(new Date(`${w.start}T00:00:00`));
      return `<div class="week" title="${w.solved} solved in the week of ${start}">
        <span class="week-count">${w.solved || ""}</span>
        <span class="week-bar" style="height:${(w.solved / maxWeek) * 60}px"></span>
        <span class="week-label">${i % 2 === 1 ? start : "&nbsp;"}</span>
      </div>`;
    }).join("");

    const tracks = data.tracks.map((tr) => {
      const rows = tr.topics.map((tp) => {
        if (!tp.attempted) return `<tr><td class="topic-name">${escapeHtml(tp.topic)}</td><td class="topic-empty" colspan="4">Not attempted yet</td></tr>`;
        const trend = tp.trend === null ? "" : tp.trend > 0.05 ? `<span class="trend-up">improving</span>` : tp.trend < -0.05 ? `<span class="trend-down">slipping</span>` : "steady";
        return `<tr>
          <td class="topic-name">${escapeHtml(tp.topic)}${tp.weak ? `<span class="weak-flag">Needs work</span>` : ""}</td>
          <td class="num">${tp.solved} of ${tp.attempted}</td>
          <td><div class="meter" role="img" aria-label="${pct(tp.solve_rate)} solved"><span class="meter-track"><span class="meter-fill" style="width:${(tp.solve_rate || 0) * 100}%"></span></span><span class="meter-value">${pct(tp.solve_rate)}</span></div></td>
          <td class="num">${one(tp.avg_hint)}</td>
          <td>${trend}</td>
        </tr>`;
      }).join("");
      return `<section class="track-block">
        <div class="track-head"><h2 class="section-title">${escapeHtml(tr.name)}</h2></div>
        <div class="table-scroll"><table class="topic-table">
          <colgroup><col class="c-topic"><col class="c-solved"><col class="c-rate"><col class="c-hints"><col class="c-trend"></colgroup>
          <thead><tr><th>Topic</th><th class="num">Solved</th><th>Solve rate</th><th class="num">Average hints</th><th>Trend</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
      </section>`;
    }).join("");

    body.innerHTML = `
      <div class="totals">
        <div class="total"><div class="total-value">${t.solved}</div><div class="total-label">problems solved</div></div>
        <div class="total"><div class="total-value">${t.solved_unaided}</div><div class="total-label">solved with no hints</div></div>
        <div class="total"><div class="total-value">${one(t.avg_hint)}</div><div class="total-label">average hints per problem</div></div>
        <div class="total"><div class="total-value">${t.flagged}</div><div class="total-label">flawed problems caught</div></div>
      </div>
      <h2 class="section-title">Problems solved per week</h2>
      <p class="section-help">Last 12 weeks, labelled by the Monday each week starts.</p>
      <div class="weeks" aria-label="Problems solved in each of the last 12 weeks">${weeks}</div>
      ${tracks}`;
  }

  // History view ----------------------------------------------------------

  const OUTCOME = { correct: "Solved", incorrect: "Not solved yet", gave_up: "Saw solution", flagged: "Flagged", open: "Not started" };

  async function showHistory() {
    const body = $("history-body");
    let rows;
    try { rows = await api("GET", "/api/history"); }
    catch (err) { body.innerHTML = `<p class="empty-page">${escapeHtml(err.message)}</p>`; return; }
    if (!rows.length) {
      body.innerHTML = `<div class="empty-page">No problems yet. <a href="#/practice">Ask for your first one</a></div>`;
      return;
    }
    const fmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    body.innerHTML = `<ul class="history-list">${rows.map((r) => {
      const outcome = r.outcome || (r.hint_level || r.submissions ? "incorrect" : "open");
      const name = state.taxonomy[r.track] ? state.taxonomy[r.track].name : r.track;
      const first = r.statement.split("\n").find((l) => l.trim()) || "";
      return `<li class="history-item"><a href="#/attempt/${r.attempt_id}">
        <span class="history-statement">${render(first)}</span>
        <span class="outcome" data-outcome="${outcome}">${OUTCOME[outcome]}</span>
        <span class="history-meta"><span>${fmt.format(new Date(r.started_at * 1000))}</span><span>${escapeHtml(name)}</span><span>${escapeHtml(r.topic)}</span>${r.hint_level ? `<span>${r.hint_level >= 4 ? "solution viewed" : `${r.hint_level} hint${r.hint_level > 1 ? "s" : ""}`}</span>` : ""}</span>
      </a></li>`;
    }).join("")}</ul>`;
  }

  // Settings view ---------------------------------------------------------

  async function showSettings() {
    const status = await api("GET", "/api/status");
    const s = status.settings;
    const models = status.models.length ? status.models : [s.reasoning_model, s.vision_model];
    const opts = (sel, current) => {
      const list = models.includes(current) ? models : [current, ...models];
      sel.innerHTML = list.map((m) => `<option ${m === current ? "selected" : ""}>${escapeHtml(m)}</option>`).join("");
    };
    opts($("set-reasoning"), s.reasoning_model);
    opts($("set-vision"), s.vision_model);
    $("set-think").checked = !!s.think;
    $("set-retries").value = s.retries;
    $("settings-saved").textContent = status.ollama ? "" : "Ollama isn't running, so installed models can't be listed.";
  }

  async function saveSettings(ev) {
    ev.preventDefault();
    await api("PUT", "/api/settings", {
      reasoning_model: $("set-reasoning").value,
      vision_model: $("set-vision").value,
      think: $("set-think").checked,
      retries: Math.max(0, Math.min(5, Number($("set-retries").value) || 0)),
    });
    $("settings-saved").textContent = "Settings saved";
    pollStatus();
  }

  // Status ----------------------------------------------------------------

  async function pollStatus() {
    const el = $("model-status");
    try {
      const s = await api("GET", "/api/status");
      if (!s.ollama) { el.dataset.state = "bad"; el.textContent = "Ollama isn't running"; }
      else if (s.missing.length) { el.dataset.state = "bad"; el.textContent = `${s.missing[0]} isn't installed`; }
      else { el.dataset.state = "ok"; el.textContent = s.settings.reasoning_model; }
    } catch (_) {
      el.dataset.state = "bad";
      el.textContent = "Server unreachable";
    }
  }

  // Routing ---------------------------------------------------------------

  async function route() {
    const hash = location.hash || "#/practice";
    const m = hash.match(/^#\/attempt\/(\d+)/);
    const parts = hash.slice(2).split("/").map((x) => { try { return decodeURIComponent(x); } catch (_) { return x; } });
    const view = m ? "practice" : parts[0] || "practice";
    for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== `view-${view}`;
    for (const a of document.querySelectorAll(".nav a")) {
      if (a.dataset.view === view) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    if (view !== "book") moveTutorHome();
    if (view === "book") showBook(parts[1], parts[2], parts.slice(3).join("/") || null);
    if (view === "progress") showProgress();
    if (view === "history") showHistory();
    if (view === "settings") showSettings();
    if (m && (!state.current || state.current.attempt_id !== Number(m[1]))) {
      try { showAttempt(await api("GET", `/api/attempts/${m[1]}`)); }
      catch (err) { showError(err.message); }
    }
  }

  // Wiring ----------------------------------------------------------------

  async function init() {
    state.taxonomy = await api("GET", "/api/taxonomy");
    fillSelect($("opt-track"), Object.keys(state.taxonomy), Object.values(state.taxonomy).map((t) => t.name));
    syncOptions();

    $("suggestions").innerHTML = SUGGESTIONS.map((s, i) => `<li><button type="button" data-i="${i}">${escapeHtml(s.text)}</button></li>`).join("");
    $("suggestions").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || isBusy()) return;
      const s = SUGGESTIONS[Number(b.dataset.i)];
      $("ask-input").value = s.text;
      generate({ request: s.text, track: s.track || null, difficulty: s.difficulty || null });
    });

    $("opt-track").addEventListener("change", syncOptions);
    $("ask-form").addEventListener("submit", (e) => {
      e.preventDefault();
      if (isBusy()) return;
      generate({
        request: $("ask-input").value,
        track: $("opt-track").value || null,
        topic: $("opt-topic").value || null,
        difficulty: $("opt-difficulty").value || null,
      });
    });
    $("busy-cancel").addEventListener("click", () => state.controller && state.controller.abort());

    $("working").addEventListener("input", () => {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(updateWorkingPreview, 150);
      scheduleSave();
    });
    $("answer").addEventListener("input", () => { updateAnswerPreview(); scheduleSave(); });
    $("answer").addEventListener("keydown", (e) => { if (e.key === "Enter" && !isBusy() && state.current) onCheck(); });

    $("act-check").addEventListener("click", onCheck);
    $("act-hint").addEventListener("click", onHint);
    $("act-solution").addEventListener("click", () => onSolution(false));
    $("act-explain").addEventListener("click", () => { $("flag-form").hidden = true; $("explain-form").hidden = !$("explain-form").hidden; if (!$("explain-form").hidden) $("explain-q").focus(); });
    $("act-flag").addEventListener("click", () => { $("explain-form").hidden = true; $("flag-form").hidden = !$("flag-form").hidden; if (!$("flag-form").hidden) $("flag-reason").focus(); });
    $("act-another").addEventListener("click", () => generate({ related_attempt: state.current.attempt_id }));
    $("act-got").addEventListener("click", () => onSelf(true));
    $("act-missed").addEventListener("click", () => onSelf(false));
    $("act-retry").addEventListener("click", onRetry);
    $("explain-form").addEventListener("submit", onExplain);
    $("flag-form").addEventListener("submit", onFlag);

    $("screenshot-btn").addEventListener("click", () => $("screenshot-file").click());
    $("screenshot-file").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) transcribe(f); e.target.value = ""; });
    $("screenshot-text").addEventListener("input", () => { $("screenshot-preview").innerHTML = render($("screenshot-text").value); });
    $("screenshot-use").addEventListener("click", importScreenshot);
    $("screenshot-discard").addEventListener("click", () => { $("screenshot-panel").hidden = true; });
    document.addEventListener("paste", (e) => {
      if ($("view-practice").hidden) return;
      const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
      if (item) { e.preventDefault(); transcribe(item.getAsFile()); }
    });
    document.addEventListener("dragover", (e) => { if (!$("view-practice").hidden) e.preventDefault(); });
    document.addEventListener("drop", (e) => {
      if ($("view-practice").hidden) return;
      const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("image/"));
      if (f) { e.preventDefault(); transcribe(f); }
    });

    $("book-text").addEventListener("click", onBookClick);
    $("book-close").addEventListener("click", () => { if (state.book) location.hash = bookHash(state.book.slug, state.book.chapter); });
    narrow.addEventListener("change", placeTutor);

    $("settings-form").addEventListener("submit", saveSettings);
    window.addEventListener("hashchange", route);

    renderSession();
    syncButtons();
    pollStatus();
    setInterval(pollStatus, 30000);
    route();
  }

  init();
})();
