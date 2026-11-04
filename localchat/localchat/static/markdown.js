/* Markdown -> safe HTML, with math (KaTeX), code highlighting and [n] citation links.
   marked turns Markdown into HTML, DOMPurify removes anything dangerous, KaTeX draws formulas. */
(function () {
  "use strict";

  marked.setOptions({ gfm: true, breaks: false });

  DOMPurify.addHook("afterSanitizeAttributes", function (node) {
    if (node.tagName === "A") {
      const href = node.getAttribute("href") || "";
      if (/^https?:\/\//i.test(href)) {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      } else if (!href.startsWith("#")) {
        node.removeAttribute("href");
      }
    }
  });

  // Code spans and fenced blocks must not be touched by the math step.
  const CODE_RE = /(^|\n)[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n[ \t]*\2[ \t]*(?=\n|$)|$)|`[^`\n]+`/g;
  const MATH_RES = [
    { re: /\$\$([\s\S]+?)\$\$/g, display: true },
    { re: /\\\[([\s\S]+?)\\\]/g, display: true },
    { re: /\\\(([\s\S]+?)\\\)/g, display: false },
    // $x$: not "$5 and $10" (no space inside the edges, no digit right after the closing $)
    { re: /(^|[^\\$\w])\$(?=\S)((?:\\.|[^$\n\\])+?)\$(?![\w$])/g, display: false, lead: true },
  ];

  function protectMath(src, store) {
    let out = "";
    let last = 0;
    src.replace(CODE_RE, function (m, _p, _f, offset) {
      out += replaceMath(src.slice(last, offset), store) + m;
      last = offset + m.length;
      return m;
    });
    return out + replaceMath(src.slice(last), store);
  }

  function replaceMath(text, store) {
    for (const rule of MATH_RES) {
      text = text.replace(rule.re, function (m, a, b) {
        const lead = rule.lead ? a : "";
        const tex = rule.lead ? b : a;
        if (rule.lead && /\s$/.test(tex)) return m;
        store.push({ tex: tex.trim(), display: rule.display });
        return lead + "%%MATH" + (store.length - 1) + "%%";
      });
    }
    return text;
  }

  function renderMath(item) {
    try {
      return katex.renderToString(item.tex, { displayMode: item.display, throwOnError: false, output: "html", strict: "ignore" });
    } catch (e) {
      return "<code>" + escapeHtml(item.tex) + "</code>";
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /** Markdown text -> sanitized HTML string. */
  function toHtml(src) {
    const maths = [];
    const text = protectMath(src || "", maths);
    let html = marked.parse(text);
    html = DOMPurify.sanitize(html, { ADD_ATTR: ["target"], FORBID_TAGS: ["style", "form", "input", "button"] });
    return html.replace(/%%MATH(\d+)%%/g, (_, i) => (maths[+i] ? renderMath(maths[+i]) : ""));
  }

  const COPY_ICON = '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>';

  /** Render into `el`, then add code-block headers, table wrappers and citation links. */
  function render(el, src, opts) {
    opts = opts || {};
    el.innerHTML = toHtml(src);
    el.querySelectorAll("pre > code").forEach(function (code) {
      const pre = code.parentElement;
      const lang = ((code.className.match(/language-([\w+#.-]+)/) || [])[1] || "").toLowerCase();
      const wrap = document.createElement("div");
      wrap.className = "codeblock";
      const head = document.createElement("div");
      head.className = "code-head";
      head.innerHTML = '<span class="lang"></span><button type="button" class="copy-code">' + COPY_ICON + "<span>Copy</span></button>";
      head.querySelector(".lang").textContent = lang || "text";
      pre.replaceWith(wrap);
      wrap.appendChild(head);
      wrap.appendChild(pre);
      if (opts.final && window.hljs) {
        try {
          if (lang && hljs.getLanguage(lang)) {
            code.innerHTML = hljs.highlight(code.textContent, { language: lang, ignoreIllegals: true }).value;
          } else if (!lang && code.textContent.length < 20000) {
            code.innerHTML = hljs.highlightAuto(code.textContent).value;
          }
          code.classList.add("hljs");
        } catch (e) { /* plain text is fine */ }
      }
    });
    el.querySelectorAll("table").forEach(function (t) {
      const w = document.createElement("div");
      w.className = "table-wrap";
      t.replaceWith(w);
      w.appendChild(t);
    });
    if (opts.sources && opts.sources.length) linkCitations(el, opts.sources);
  }

  /** Turn "[3]" in the text into a small link to source 3. */
  function linkCitations(el, sources) {
    const byN = {};
    sources.forEach((s) => (byN[s.n] = s));
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!/\[\d{1,3}(?:\s*,\s*\d{1,3})*\]/.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
        if (node.parentElement.closest("code, pre, a, .katex")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      const frag = document.createDocumentFragment();
      let last = 0;
      const text = node.nodeValue;
      text.replace(/\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g, function (m, list, offset) {
        const nums = list.split(",").map((x) => +x.trim());
        if (!nums.every((n) => byN[n])) return m;
        frag.appendChild(document.createTextNode(text.slice(last, offset)));
        nums.forEach(function (n) {
          const a = document.createElement("a");
          a.className = "cite";
          a.href = byN[n].url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = n;
          a.title = byN[n].title + "\n" + byN[n].url;
          frag.appendChild(a);
        });
        last = offset + m.length;
        return m;
      });
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.replaceWith(frag);
    });
  }

  window.MD = { render, toHtml, escapeHtml };
})();
