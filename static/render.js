// Markdown + KaTeX rendering. Maths is lifted out before Markdown runs, so Markdown
// cannot mangle underscores and backslashes, then typeset back into place.
(function () {
  const MATH_PATTERNS = [
    { re: /\$\$([\s\S]+?)\$\$/g, display: true },
    { re: /\\\[([\s\S]+?)\\\]/g, display: true },
    { re: /\\begin\{(align\*?|aligned|equation\*?|gather\*?|cases)\}[\s\S]*?\\end\{\1\}/g, display: true, whole: true },
    { re: /\\\(([\s\S]+?)\\\)/g, display: false },
    { re: /(?<![\\$])\$(?!\$)((?:\\.|[^$\\\n])+?)\$/g, display: false },
  ];

  marked.setOptions({ gfm: true, breaks: false });

  function typeset(tex, display) {
    try {
      return katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: "ignore", trust: false });
    } catch (e) {
      return `<code>${escapeHtml(tex)}</code>`;
    }
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function render(source) {
    if (!source) return "";
    const stash = [];
    const hold = (html) => `KXMATH${stash.push(html) - 1}XK`;

    // Code fences first; a ```plot fence is reserved for graphs in a later version.
    let text = source.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, body) => {
      if (lang === "plot") {
        return hold(`<figure class="plot-placeholder"><figcaption>A graph belongs here. Graphs arrive in a later version.</figcaption></figure>`);
      }
      return hold(`<pre><code>${escapeHtml(body)}</code></pre>`);
    });

    for (const { re, display, whole } of MATH_PATTERNS) {
      text = text.replace(re, (match, inner) => hold(typeset(whole ? match : inner, display)));
    }

    let html = marked.parse(text);
    html = DOMPurify.sanitize(html);
    return html.replace(/KXMATH(\d+)XK/g, (_, i) => stash[Number(i)]);
  }

  // A single inline expression, for the final-answer preview.
  function renderInline(tex) {
    const t = (tex || "").trim().replace(/^\$+|\$+$/g, "");
    return t ? typeset(t, false) : "";
  }

  // AoPS-style answer line: (A) 1  (B) 2 ...  Each option is its own span so the line can wrap.
  function renderChoices(choices) {
    if (!choices || !choices.length) return "";
    return choices
      .map((c, i) => `<span class="choice">${typeset(`\\textbf{(${"ABCDE"[i]})}\\ ${c}`, false)}</span>`)
      .join("");
  }

  window.MathRender = { render, renderInline, renderChoices, escapeHtml };
})();
