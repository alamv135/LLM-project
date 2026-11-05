/* MedScout Ask - the chat page. Talks to medscout/ask/routes.py through /api/ask/... */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const API = "/api/ask";
  const isWebUrl = (u) => /^https?:\/\//i.test(u || "");
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const ICON = {
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg>',
    retry: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5"/></svg>',
    globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
    page: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    filesearch: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4"/><path d="M14 3v5h5v3"/><circle cx="16.5" cy="16.5" r="3"/><path d="m21 21-2.4-2.4"/></svg>',
    chevron: '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
    bulb: '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z"/></svg>',
    file: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    pencil: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
    db: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    flask: '<svg viewBox="0 0 24 24"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3"/><path d="M7.5 15h9"/></svg>',
    warn: '<svg viewBox="0 0 24 24"><path d="M12 3 2 20h20zM12 10v4M12 17h.01"/></svg>',
  };

  const state = {
    settings: {},
    status: null,
    chats: [],
    chatId: null,
    chat: null,          // the open chat {id, title, messages}
    busy: false,
    abort: null,
    pending: [],         // attachments in the composer: {key, name, kind, status, meta, url, error}
    live: null,          // the answer being streamed
    stickBottom: true,
  };

  // ----- small helpers --------------------------------------------------------------------------
  async function api(path, opts) {
    opts = opts || {};
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.json !== undefined) {
      init.method = opts.method || "POST";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    }
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
    return data;
  }

  function toast(msg, ms) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), ms || 3500);
  }

  function fmtBytes(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    if (n < 1024 ** 3) return (n / 1024 / 1024).toFixed(1) + " MB";
    return (n / 1024 ** 3).toFixed(1) + " GB";
  }

  function domain(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch (e) { return url; }
  }

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = el("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    if (btn) {
      const old = btn.innerHTML;
      btn.innerHTML = ICON.check + (btn.dataset.label ? "<span>Copied</span>" : "");
      setTimeout(() => (btn.innerHTML = old), 1400);
    }
  }

  // ----- theme ----------------------------------------------------------------------------------
  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
  }

  // ----- status / models --------------------------------------------------------------------------
  async function refreshStatus() {
    try {
      state.status = await api(API + "/status");
    } catch (e) {
      state.status = { ollama: false, ollama_error: "The MedScout server is not responding." };
    }
    renderStatus();
  }

  function renderStatus() {
    const s = state.status || {};
    const box = $("#ollama-status");
    box.classList.toggle("ok", !!s.ollama);
    box.classList.toggle("bad", !s.ollama);
    box.querySelector(".label").textContent = s.ollama ? "Ollama " + (s.ollama_version || "") : "Ollama not running";
    box.title = s.ollama ? s.ollama_url : s.ollama_error || "";

    const sel = $("#model-select");
    const current = state.settings.model || s.model;
    sel.innerHTML = "";
    const auto = el("option", null, "Auto: local first, API only when needed");
    auto.value = "auto";
    sel.appendChild(auto);
    const catalog = s.catalog || [];
    [["Local (free, private)", catalog.filter((m) => m.local)], ["API via OpenRouter (paid)", catalog.filter((m) => !m.local)]].forEach(([label, list]) => {
      if (!list.length) return;
      const g = document.createElement("optgroup");
      g.label = label;
      list.forEach((m) => {
        const o = el("option", null, m.label + (m.local ? "" : "  ·  $" + Number(m.price_out).toFixed(2) + "/M out") + (m.available ? "" : "  (" + (m.local ? "not downloaded" : "no key") + ")"));
        o.value = m.alias;
        g.appendChild(o);
      });
      sel.appendChild(g);
    });
    if (current && current !== "auto" && !catalog.some((m) => m.alias === current)) {
      const o = el("option", null, current);
      o.value = current;
      sel.appendChild(o);
    }
    sel.value = current;
    state.currentModel = catalog.find((m) => m.alias === current) || null;
    $("#welcome-sub").textContent = current === "auto"
      ? "Ask anything. Easy questions stay on your Mac; harder ones may use an API model (your files stay local)."
      : "Ask anything. " + ((state.currentModel && state.currentModel.label) || current) + " answers; it can check the web, FDA, ClinicalTrials.gov and PubMed.";
    $("#db-toggle").hidden = !(s.database && s.database.connected);
    $("#db-toggle").title = s.database && s.database.connected ? "Let the model read your database " + s.database.name + " (read-only)" : "";
    syncPrivate();

    const caps = s.capabilities || [];
    const hasCaps = caps.length > 0;
    const web = $("#web-toggle");
    const think = $("#think-toggle");
    const med = $("#medtech-toggle");
    web.disabled = hasCaps && !caps.includes("tools");
    med.disabled = web.disabled;
    med.title = med.disabled ? "This model can't use tools" : "Let the model search openFDA (510(k), PMA, classification), ClinicalTrials.gov and PubMed";
    think.disabled = hasCaps && !caps.includes("thinking");
    web.title = web.disabled ? "This model can't use tools, so it can't search" : "Let the model search the web and read pages";
    think.title = think.disabled ? "This model has no thinking mode" : "Let the model reason step by step before answering (slower, better for hard questions)";
    syncToggles();

    const banner = $("#banner");
    banner.innerHTML = "";
    banner.hidden = true;
    if (!s.ollama) {
      banner.hidden = false;
      banner.className = "banner bad";
      banner.innerHTML = ICON.warn + "<div><b>Can't reach Ollama.</b> Open the Ollama app (or run <code>ollama serve</code> in Terminal). <span class='muted'></span></div>";
      banner.querySelector(".muted").textContent = s.ollama_error || "";
      const b = el("button", "btn small", "Try again");
      b.onclick = refreshStatus;
      banner.appendChild(b);
    } else if (s.key_missing) {
      banner.hidden = false;
      banner.className = "banner";
      banner.innerHTML = ICON.warn + "<div><b></b> needs an OpenRouter API key. Add it in the console's <b>Model connection</b> tab, or pick a local model or Auto.</div>";
      banner.querySelector("div b").textContent = (state.currentModel && state.currentModel.label) || current;
    } else if (!s.model_installed) {
      const pull = s.download_model || current;
      banner.hidden = false;
      banner.className = "banner";
      const text = el("div");
      text.innerHTML = "<b></b> is not downloaded yet" + (current === "auto" ? " (Auto uses it for easy questions and to rate questions)" : "") +
        ". It is a one-time download" + (/^qwen3\.5:9b$/.test(pull) ? " of about 6.6 GB." : ".");
      text.querySelector("b").textContent = pull;
      const bar = el("div", "progress");
      bar.hidden = true;
      bar.appendChild(el("span"));
      text.appendChild(bar);
      const b = el("button", "btn small primary", "Download");
      b.onclick = () => pullModel(pull, b, bar, text);
      banner.appendChild(document.createRange().createContextualFragment(ICON.warn));
      banner.appendChild(text);
      banner.appendChild(b);
    }
  }

  async function pullModel(model, btn, bar, text) {
    btn.disabled = true;
    btn.textContent = "Downloading…";
    bar.hidden = false;
    const info = el("small", "muted");
    text.appendChild(info);
    try {
      const res = await fetch(API + "/pull", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "HTTP " + res.status);
      await readLines(res, (ev) => {
        if (ev.error) throw new Error(ev.error);
        if (ev.total && ev.completed != null) {
          const pct = Math.floor((100 * ev.completed) / ev.total);
          bar.firstChild.style.width = pct + "%";
          info.textContent = ev.status + " · " + pct + "% of " + fmtBytes(ev.total);
        } else if (ev.status) {
          info.textContent = ev.status;
        }
      });
      toast(model + " is ready");
      await refreshStatus();
    } catch (e) {
      info.textContent = "Download failed: " + e.message;
      btn.disabled = false;
      btn.textContent = "Try again";
    }
  }

  // ----- settings --------------------------------------------------------------------------------
  async function loadSettings() {
    state.settings = await api(API + "/settings");
    applyTheme(state.settings.theme);
  }

  async function saveSettings(changes) {
    state.settings = await api(API + "/settings", { json: changes });
    applyTheme(state.settings.theme);
  }

  function chatIsPrivate() {
    const msgs = (state.chat && state.chat.messages) || [];
    return state.pending.length > 0 || msgs.some((m) => (m.role === "user" && m.attachments && m.attachments.length) ||
      (m.parts || []).some((p) => p.type === "tool" && /^db_/.test(p.name || ""))) ||
      $("#db-toggle").getAttribute("aria-pressed") === "true";
  }
  function allowCloud() {
    return state.chat ? !!state.chat.allow_cloud : !!state.allowCloudNew;
  }
  function syncPrivate() {
    const p = $("#private-toggle");
    const cloudPossible = (state.settings.model || "auto") === "auto" || (state.currentModel && !state.currentModel.local);
    p.hidden = !(cloudPossible && chatIsPrivate());
    const local = !allowCloud();
    p.setAttribute("aria-pressed", String(local));
    p.querySelector("span").textContent = local ? "Files stay local" : "Cloud may read files";
  }

  function syncToggles() {
    const web = $("#web-toggle");
    const think = $("#think-toggle");
    web.setAttribute("aria-pressed", String(!!state.settings.web_search && !web.disabled));
    $("#db-toggle").setAttribute("aria-pressed", String(!!state.dbOn));
    const med = $("#medtech-toggle");
    med.setAttribute("aria-pressed", String(!!state.settings.medtech && !med.disabled));
    const demo = state.settings.medtech_backend && state.settings.medtech_backend !== "live";
    med.querySelector("span").textContent = demo ? "FDA & trials (demo data)" : "FDA & trials";
    think.setAttribute("aria-pressed", String(!!state.settings.think && !think.disabled));
  }

  function openSettings() {
    const f = $("#settings-form");
    const s = state.settings;
    const ctx = f.elements.num_ctx;
    ctx.innerHTML = "";
    const choices = ((state.status && state.status.context_choices) || [8192, 16384, 32768, 65536, 131072]).slice();
    if (!choices.includes(s.num_ctx)) choices.push(s.num_ctx);
    choices.sort((a, b) => a - b).forEach((n) => {
      const o = el("option", null, Math.round(n / 1024) + "K tokens" + (n === 32768 ? " (recommended)" : ""));
      o.value = n;
      ctx.appendChild(o);
    });
    ctx.value = s.num_ctx;
    f.elements.model.value = s.model || "";
    const dl = $("#model-names");
    dl.innerHTML = "";
    ((state.status && state.status.models) || []).forEach((m) => { const o = el("option"); o.value = m.name; dl.appendChild(o); });
    f.elements.ollama_url.value = s.ollama_url || "";
    $("#ollama-url-state").textContent = s.ollama_url ? "Leave empty to use the console's Model connection tab."
      : "Now using " + (s.ollama_url_effective || "http://127.0.0.1:11434") + " (from the console's Model connection tab, or the default).";
    const mb = f.elements.medtech_backend;
    mb.innerHTML = "";
    [["live", "Live: the real public databases"]].concat(((state.status && state.status.demo_cases) || []).map((c) => [c, "Demo case: " + c + " (offline, synthetic)"]))
      .forEach(([v, l]) => { const o = el("option", null, l); o.value = v; mb.appendChild(o); });
    mb.value = s.medtech_backend || "live";
    f.elements.temperature.value = s.temperature == null ? "" : s.temperature;
    f.elements.max_cost_per_answer.value = s.max_cost_per_answer == null ? 0.1 : s.max_cost_per_answer;
    f.elements.search_backend.value = s.search_backend;
    f.elements.ollama_api_key.value = "";
    f.elements.ollama_api_key_clear.checked = false;
    f.elements.ollama_api_key_clear.parentElement.hidden = !(s.ollama_api_key_set && !s.ollama_api_key_from_env);
    $("#key-state").textContent = s.ollama_api_key_from_env
      ? "Using the key from the OLLAMA_API_KEY environment variable."
      : s.ollama_api_key_set
        ? "A key is saved. Paste a new one to replace it."
        : "More reliable search. Free with an Ollama account. Saved only on this Mac.";
    f.elements.max_tool_rounds.value = s.max_tool_rounds;
    f.elements.custom_instructions.value = s.custom_instructions || "";
    f.elements.theme.value = s.theme || "system";
    $("#data-dir").textContent = s.data_dir || "";
    $("#settings").returnValue = "";
    $("#settings").showModal();
  }

  async function submitSettings() {
    const f = $("#settings-form").elements;
    const changes = {
      model: f.model.value.trim() || state.settings.model,
      ollama_url: f.ollama_url.value,
      medtech_backend: f.medtech_backend.value,
      num_ctx: +f.num_ctx.value,
      temperature: f.temperature.value === "" ? null : +f.temperature.value,
      max_cost_per_answer: f.max_cost_per_answer.value === "" ? 0 : +f.max_cost_per_answer.value,
      search_backend: f.search_backend.value,
      max_tool_rounds: +f.max_tool_rounds.value || 6,
      custom_instructions: f.custom_instructions.value,
      theme: f.theme.value,
    };
    if (f.ollama_api_key.value.trim()) changes.ollama_api_key = f.ollama_api_key.value.trim();
    if (f.ollama_api_key_clear.checked) changes.ollama_api_key_clear = true;
    try {
      await saveSettings(changes);
      toast("Settings saved");
      refreshStatus();
    } catch (e) {
      toast("Could not save: " + e.message);
    }
  }

  // ----- sidebar: chat list ----------------------------------------------------------------------
  async function loadChats() {
    state.chats = (await api(API + "/chats")).chats;
    renderChatList();
  }

  function renderChatList() {
    const list = $("#chat-list");
    const filter = $("#chat-filter").value.trim().toLowerCase();
    list.innerHTML = "";
    const now = new Date();
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    const groups = [
      ["Today", startToday],
      ["Yesterday", startToday - 86400],
      ["Previous 7 days", startToday - 7 * 86400],
      ["Previous 30 days", startToday - 30 * 86400],
      ["Older", -Infinity],
    ];
    let currentGroup = null;
    const chats = state.chats.filter((c) => !filter || (c.title || "").toLowerCase().includes(filter));
    if (!chats.length) {
      list.appendChild(el("p", "empty-list", filter ? "No chats match." : "Your chats will appear here."));
      return;
    }
    chats.forEach((c) => {
      const g = groups.find(([, t]) => c.updated >= t)[0];
      if (g !== currentGroup) {
        list.appendChild(el("div", "group", g));
        currentGroup = g;
      }
      const item = el("div", "chat-item" + (c.id === state.chatId ? " active" : ""));
      const a = el("a", "chat-link", c.title || "Chat");
      a.href = "#/c/" + c.id;
      a.title = c.title || "";
      const actions = el("div", "chat-actions");
      const ren = el("button", "icon-btn tiny");
      ren.innerHTML = ICON.pencil;
      ren.title = "Rename";
      ren.onclick = (e) => { e.preventDefault(); renameChat(c, item, a); };
      const del = el("button", "icon-btn tiny");
      del.innerHTML = ICON.trash;
      del.title = "Delete";
      del.onclick = (e) => { e.preventDefault(); deleteChat(c); };
      actions.append(ren, del);
      item.append(a, actions);
      list.appendChild(item);
    });
  }

  function renameChat(c, item, link) {
    const input = el("input", "rename");
    input.value = c.title || "";
    link.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const title = input.value.trim();
      if (save && title && title !== c.title) {
        try {
          await api(API + "/chats/" + c.id + "/rename", { json: { title } });
          c.title = title;
          if (state.chat && state.chat.id === c.id) {
            state.chat.title = title;
            $("#chat-title").textContent = title;
          }
        } catch (e) { toast(e.message); }
      }
      renderChatList();
    };
    input.onkeydown = (e) => {
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") finish(false);
    };
    input.onblur = () => finish(true);
  }

  async function deleteChat(c) {
    if (!confirm('Delete "' + (c.title || "this chat") + '"? Its uploaded files are deleted too.')) return;
    try {
      await api(API + "/chats/" + c.id, { method: "DELETE" });
    } catch (e) { toast(e.message); }
    state.chats = state.chats.filter((x) => x.id !== c.id);
    if (state.chatId === c.id) newChat();
    renderChatList();
  }

  function upsertChatListItem(info) {
    const i = state.chats.findIndex((c) => c.id === info.id);
    if (i >= 0) state.chats.splice(i, 1);
    state.chats.unshift({ id: info.id, title: info.title, updated: info.updated || Date.now() / 1000 });
    renderChatList();
  }

  // ----- opening chats ----------------------------------------------------------------------------
  function newChat() {
    if (state.busy) stopGenerating();
    state.chatId = null;
    state.chat = null;
    state.allowCloudNew = false;
    state.dbOn = false;
    syncToggles();
    if (location.hash) history.pushState(null, "", location.pathname);
    renderChat();
    renderChatList();
    closeSidebarIfOverlay();
    $("#input").focus();
  }

  async function openChat(id) {
    if (state.busy && state.chatId !== id) stopGenerating();
    try {
      state.chat = await api(API + "/chats/" + id);
      state.chatId = id;
    } catch (e) {
      toast("Chat not found");
      return newChat();
    }
    state.dbOn = (state.chat.messages || []).some((m) => (m.parts || []).some((p) => p.type === "tool" && /^db_/.test(p.name || "")));
    syncToggles();
    renderChat();
    renderChatList();
    closeSidebarIfOverlay();
    syncPrivate();
    state.stickBottom = true;
    scrollToBottom(true);
    // pictures and math fonts load a moment later and make the page taller
    $("#messages").querySelectorAll("img").forEach((img) => img.addEventListener("load", () => scrollToBottom(false), { once: true }));
    setTimeout(() => scrollToBottom(false), 150);
  }

  async function attachRun(runId) {
    history.replaceState(null, "", location.pathname);
    newChat();
    const item = { key: "run-" + runId, name: "MedScout brief (" + runId + ")", status: "uploading", meta: null, url: null };
    state.pending.push(item);
    renderPending();
    try {
      item.meta = await api(API + "/attach-run", { json: { run_id: runId } });
      item.name = item.meta.name;
      item.status = "ready";
      if (!$("#input").value.trim()) {
        $("#input").value = "Summarise this MedScout brief: the main findings, the weakest claims and what I should check next.";
        autoGrow();
      }
    } catch (e) {
      item.status = "error";
      item.error = e.message;
    }
    renderPending();
    $("#input").focus();
  }

  function route() {
    const run = location.hash.match(/^#attach-run=([0-9a-f-]+)$/);
    if (run) return attachRun(run[1]);
    const m = location.hash.match(/^#\/c\/([\w-]+)$/);
    if (m) {
      if (m[1] !== state.chatId) openChat(m[1]);
    } else if (state.chatId) {
      newChat();
    }
  }

  function setGreeting() {
    const h = new Date().getHours();
    $("#greeting").textContent = h < 5 ? "Working late?" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  }

  function renderChat() {
    const box = $("#messages");
    box.innerHTML = "";
    const msgs = (state.chat && state.chat.messages) || [];
    $("#main").classList.toggle("empty", msgs.length === 0 && !state.busy);
    $("#chat-title").textContent = (state.chat && state.chat.title) || "";
    document.title = state.chat ? state.chat.title + " · MedScout Ask" : "MedScout Ask";
    setGreeting();
    msgs.forEach((m, i) => box.appendChild(m.role === "user" ? userView(m) : assistantView(m, i === msgs.length - 1).root));
  }

  // ----- message views ---------------------------------------------------------------------------
  function userView(m) {
    const root = el("div", "msg user");
    const bubble = el("div", "bubble");
    if (m.attachments && m.attachments.length) {
      const row = el("div", "att-row");
      m.attachments.forEach((a) => row.appendChild(attachmentChip(a, API + "/files/" + a.id)));
      bubble.appendChild(row);
    }
    if (m.content) bubble.appendChild(el("div", "user-text", m.content));
    root.appendChild(bubble);
    const actions = el("div", "msg-actions");
    const copy = el("button", "icon-btn tiny");
    copy.innerHTML = ICON.copy;
    copy.title = "Copy";
    copy.onclick = () => copyText(m.content || "", copy);
    actions.appendChild(copy);
    root.appendChild(actions);
    return root;
  }

  function attachmentChip(a, imgUrl) {
    if (a.kind === "image" && imgUrl) {
      const link = el("a", "att-thumb");
      link.href = imgUrl;
      link.target = "_blank";
      link.title = a.name;
      const img = el("img");
      img.src = imgUrl;
      img.alt = a.name;
      link.appendChild(img);
      return link;
    }
    const chip = el("div", "att-chip");
    chip.innerHTML = ICON.file;
    const t = el("div", "att-text");
    t.appendChild(el("div", "att-name", a.name));
    const ext = (a.name.split(".").pop() || "").toUpperCase();
    t.appendChild(el("div", "att-meta", [ext, a.chars ? a.chars.toLocaleString() + " chars" : fmtBytes(a.size)].filter(Boolean).join(" · ")));
    chip.appendChild(t);
    if (a.note) chip.title = a.note;
    return chip;
  }

  /** The view of one assistant message. Returns an object that can be updated while streaming. */
  function assistantView(m, isLast) {
    const root = el("div", "msg assistant");
    const body = el("div", "a-body");
    root.appendChild(body);
    const partsBox = el("div", "parts");
    body.appendChild(partsBox);
    const view = { root, body, partsBox, partEls: [], message: m };
    (m.parts || []).forEach((p) => addPartEl(view, p, true));
    if (!m.parts && m.content) addPartEl(view, { type: "text", text: m.content }, true);
    finishView(view, m, isLast);
    return view;
  }

  function addPartEl(view, p, final) {
    let node;
    if (p.type === "thinking") {
      node = el("details", "thinking");
      const sum = el("summary");
      sum.innerHTML = ICON.bulb + '<span class="t-label"></span><span class="t-preview"></span>' + ICON.chevron;
      node.appendChild(sum);
      node.appendChild(el("div", "thinking-text"));
      updateThinking(node, p, final);
    } else if (p.type === "tool") {
      node = el("div", "tool");
      updateTool(node, p);
    } else {
      node = el("div", "md");
      MD.render(node, p.text, { final, sources: view.message.sources });
    }
    view.partsBox.appendChild(node);
    view.partEls.push(node);
    return node;
  }

  function updateThinking(node, p, final) {
    const secs = p.ms != null ? Math.max(1, Math.round(p.ms / 1000)) : null;
    node.querySelector(".t-label").textContent = final || p.ms != null ? "Thought for " + secs + (secs === 1 ? " second" : " seconds") : "Thinking…";
    node.classList.toggle("active", !(final || p.ms != null));
    const lines = (p.text || "").trim().split("\n").filter(Boolean);
    node.querySelector(".t-preview").textContent = !(final || p.ms != null) && lines.length ? lines[lines.length - 1].slice(-110) : "";
    node.querySelector(".thinking-text").textContent = p.text || "";
  }

  function updateTool(node, p) {
    node.innerHTML = "";
    node.classList.toggle("pending", !!p.pending);
    node.classList.toggle("failed", p.ok === false);
    const head = el("button", "tool-head");
    head.type = "button";
    const icon = p.name === "web_search" ? ICON.globe : p.name === "fetch_url" ? ICON.page : p.name === "search_files" ? ICON.filesearch : /^db_/.test(p.name) ? ICON.db : ICON.flask;
    head.innerHTML = '<span class="tool-icon">' + icon + '</span><span class="tool-label"></span><span class="tool-count"></span>' + ICON.chevron;
    let label = p.label || p.name;
    if (p.pending) label = label.replace(/^Searched/, "Searching").replace(/^Read /, "Reading ") + "…";
    head.querySelector(".tool-label").textContent = label;
    const results = p.results || [];
    if (p.ok === false) head.querySelector(".tool-count").textContent = "failed";
    else if (p.name !== "fetch_url" && p.name !== "search_files" && !p.pending) head.querySelector(".tool-count").textContent = results.length + (results.length === 1 ? " result" : " results");
    node.appendChild(head);
    const list = el("div", "tool-body");
    list.hidden = true;
    if (p.error) list.appendChild(el("p", "tool-error", p.error));
    results.forEach((r) => {
      const a = el(isWebUrl(r.url) ? "a" : "div", "result");
      if (isWebUrl(r.url)) {
        a.href = r.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      }
      a.appendChild(el("span", "r-n", String(r.n)));
      const t = el("span", "r-text");
      t.appendChild(el("span", "r-title", r.title || r.url));
      t.appendChild(el("span", "r-domain", domain(r.url)));
      a.appendChild(t);
      list.appendChild(a);
    });
    if (!p.pending && (results.length || p.error)) {
      head.onclick = () => {
        list.hidden = !list.hidden;
        node.classList.toggle("open", !list.hidden);
      };
    } else {
      head.disabled = true;
    }
    node.appendChild(list);
  }

  function strongerExists(tier) {
    return ((state.status && state.status.catalog) || []).some((c) => c.tier > (tier || 0) && c.available);
  }

  function routeLine(r) {
    const d = el("div", "route-line");
    d.textContent = "→ " + r.label + (r.local && !/local/i.test(r.label) ? " (local)" : "") + " · " + r.reason;
    d.title = (r.skipped || []).join("\n");
    return d;
  }

  function finishView(view, m, isLast) {
    const { body } = view;
    body.querySelectorAll(".a-foot, .notice, .error-box, .sources, .route-line").forEach((n) => n.remove());
    if (m.route && m.route.reason && m.route.reason !== "chosen by you") body.insertBefore(routeLine(m.route), view.partsBox);
    (m.notices || []).forEach((n) => body.appendChild(el("div", "notice", n)));
    if (m.error) {
      const box = el("div", "error-box");
      box.innerHTML = ICON.warn;
      box.appendChild(el("span", null, m.error));
      body.appendChild(box);
    }
    const sources = m.sources || [];
    if (sources.length) {
      const cited = new Set();
      (m.content || "").replace(/\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g, (_, list) => list.split(",").forEach((x) => cited.add(+x.trim())));
      let shown = sources.filter((s) => cited.has(s.n));
      if (!shown.length) shown = sources;
      const box = el("div", "sources");
      box.appendChild(el("span", "sources-label", "Sources"));
      shown.forEach((s) => {
        const a = el(isWebUrl(s.url) ? "a" : "span", "source-chip");
        if (isWebUrl(s.url)) {
          a.href = s.url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
        }
        a.title = s.title + "\n" + s.url;
        a.appendChild(el("span", "s-n", String(s.n)));
        a.appendChild(el("span", "s-domain", domain(s.url)));
        box.appendChild(a);
      });
      body.appendChild(box);
    }
    const foot = el("div", "a-foot");
    const copy = el("button", "icon-btn tiny");
    copy.innerHTML = ICON.copy;
    copy.title = "Copy";
    copy.onclick = () => copyText(m.content || "", copy);
    foot.appendChild(copy);
    if (isLast) {
      const retry = el("button", "icon-btn tiny");
      retry.innerHTML = ICON.retry;
      retry.title = "Retry";
      retry.onclick = () => send({ retry: true });
      foot.appendChild(retry);
    }
    if (isLast && m.route && m.route.model && !m.error && strongerExists(m.route.tier) && !(chatIsPrivate() && !allowCloud())) {
      const up = el("button", "btn small stronger", "Try a stronger model");
      up.title = "Answer again with a more capable (API) model. Costs tokens.";
      up.onclick = () => send({ retry: true, escalate_from: m.route.model });
      foot.appendChild(up);
    }
    const st = m.stats || {};
    const bits = [m.model_label || m.model];
    if (st.cost_usd) bits.push("$" + st.cost_usd.toFixed(4));
    else if (m.route && m.route.local) bits.push("free");
    if (m.stopped) bits.push("stopped");
    if (st.tokens) bits.push(st.tokens.toLocaleString() + " tokens");
    if (st.tokens_per_s && !(m.route && m.route.local === false)) bits.push(st.tokens_per_s + " tok/s");
    if (st.seconds != null) bits.push(st.seconds + " s");
    const stats = el("span", "stats", bits.filter(Boolean).join(" · "));
    if (st.context_used && st.num_ctx) stats.title = "Context used: " + st.context_used.toLocaleString() + " of " + st.num_ctx.toLocaleString() + " tokens";
    foot.appendChild(stats);
    body.appendChild(foot);
  }

  // ----- sending and streaming --------------------------------------------------------------------
  async function readLines(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
    if (buf.trim()) onEvent(JSON.parse(buf));
  }

  function setBusy(b) {
    state.busy = b;
    $("#send-btn").classList.toggle("stop", b);
    $("#send-btn").title = b ? "Stop (Esc)" : "Send (Enter)";
    $("#send-btn").setAttribute("aria-label", b ? "Stop" : "Send");
    updateSendEnabled();
  }

  function updateSendEnabled() {
    const uploading = state.pending.some((p) => p.status === "uploading");
    const hasContent = $("#input").value.trim() || state.pending.some((p) => p.status === "ready");
    $("#send-btn").disabled = !state.busy && (uploading || !hasContent);
  }

  async function send(opts) {
    opts = opts || {};
    if (state.busy) return;
    const input = $("#input");
    const text = opts.retry ? "" : input.value.trim();
    const ready = state.pending.filter((p) => p.status === "ready");
    if (!opts.retry && !text && !ready.length) return;
    if (state.pending.some((p) => p.status === "uploading")) return toast("Wait for the upload to finish");

    const box = $("#messages");
    $("#main").classList.remove("empty");
    if (opts.retry) {
      const last = box.lastElementChild;
      if (last && last.classList.contains("assistant")) last.remove();
      if (state.chat) while (state.chat.messages.length && state.chat.messages[state.chat.messages.length - 1].role === "assistant") state.chat.messages.pop();
    } else {
      const userMsg = { role: "user", content: text, attachments: ready.map((p) => p.meta) };
      if (state.chat) state.chat.messages.push(userMsg);
      else state.firstMessage = userMsg;
      const v = userView(userMsg);
      // show local previews for images that were just attached
      v.querySelectorAll(".att-thumb img").forEach((img, i) => {
        const pic = ready.filter((p) => p.meta.kind === "image")[i];
        if (pic && pic.url) img.src = pic.url;
      });
      box.appendChild(v);
      input.value = "";
      autoGrow();
      state.pending = [];
      renderPending();
    }
    // the previous answer is no longer the last one: hide its retry button
    box.querySelectorAll(".msg.assistant .a-foot .icon-btn:nth-child(2), .msg.assistant .a-foot .stronger").forEach((b) => b.remove());

    const live = { message: { role: "assistant", parts: [], sources: [], content: "" }, view: null };
    live.view = { root: el("div", "msg assistant streaming"), partEls: [] };
    live.view.body = el("div", "a-body");
    live.view.partsBox = el("div", "parts");
    live.view.message = live.message;
    live.view.root.appendChild(live.view.body);
    live.view.body.appendChild(live.view.partsBox);
    const typing = el("div", "typing");
    typing.innerHTML = "<span></span><span></span><span></span>";
    live.view.body.appendChild(typing);
    box.appendChild(live.view.root);
    state.live = live;
    state.stickBottom = true;
    scrollToBottom(true);
    setBusy(true);

    const ctrl = new AbortController();
    state.abort = ctrl;
    const payload = {
      chat_id: state.chatId,
      text,
      attachments: opts.retry ? [] : ready.map((p) => p.meta.id),
      retry: !!opts.retry,
      model: $("#model-select").value || state.settings.model,
      web_search: $("#web-toggle").getAttribute("aria-pressed") === "true",
      think: $("#think-toggle").getAttribute("aria-pressed") === "true",
      medtech: $("#medtech-toggle").getAttribute("aria-pressed") === "true",
      database: $("#db-toggle").getAttribute("aria-pressed") === "true" && !$("#db-toggle").hidden,
      allow_cloud: allowCloud(),
      escalate_from: opts.escalate_from || "",
    };
    let finished = false;
    try {
      const res = await fetch(API + "/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: ctrl.signal });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "HTTP " + res.status);
      await readLines(res, (ev) => {
        if (ev.type === "done") finished = true;
        handleEvent(live, ev, typing);
      });
    } catch (e) {
      if (e.name !== "AbortError") live.message.error = "Connection problem: " + e.message;
    }
    typing.remove();
    if (!finished) {
      live.message.stopped = true;
      flushLive(live, true);
      finishView(live.view, live.message, true);
    }
    live.view.root.classList.remove("streaming");
    state.live = null;
    state.abort = null;
    setBusy(false);
    if (state.chatId) refreshSidebarSoon();
  }

  function handleEvent(live, ev, typing) {
    const m = live.message;
    switch (ev.type) {
      case "chat":
        if (state.chatId !== ev.chat.id) {
          state.chatId = ev.chat.id;
          state.chat = { id: ev.chat.id, title: ev.chat.title, messages: state.firstMessage ? [state.firstMessage] : [], allow_cloud: !!state.allowCloudNew };
          state.firstMessage = null;
          history.replaceState(null, "", "#/c/" + ev.chat.id);
        }
        $("#chat-title").textContent = ev.chat.title;
        document.title = ev.chat.title + " · MedScout Ask";
        upsertChatListItem(ev.chat);
        break;
      case "thinking":
      case "text": {
        typing.hidden = true;
        const last = m.parts[m.parts.length - 1];
        if (last && last.type === ev.type) last.text += ev.text;
        else m.parts.push({ type: ev.type, text: ev.text, _new: true });
        if (ev.type === "text") m.content += ev.text;
        scheduleFlush(live);
        break;
      }
      case "thinking_done": {
        const t = [...m.parts].reverse().find((p) => p.type === "thinking");
        if (t) t.ms = ev.ms;
        scheduleFlush(live);
        break;
      }
      case "tool_start":
        typing.hidden = true;
        m.parts.push({ type: "tool", id: ev.id, name: ev.name, label: ev.label, args: ev.args, pending: true, _new: true });
        scheduleFlush(live);
        break;
      case "tool_end": {
        const p = m.parts.find((x) => x.type === "tool" && x.id === ev.id);
        if (p) Object.assign(p, { pending: false, ok: ev.ok, label: ev.label, results: ev.results, error: ev.error, _dirty: true });
        (ev.results || []).forEach((r) => { if (!m.sources.find((s) => s.n === r.n)) m.sources.push(r); });
        typing.hidden = false;
        scheduleFlush(live);
        break;
      }
      case "notice":
        live.view.body.insertBefore(el("div", "notice", ev.text), live.view.partsBox);
        break;
      case "route":
        if (ev.reason !== "chosen by you") live.view.body.insertBefore(routeLine(ev), live.view.partsBox);
        break;
      case "context":
        live.view.body.insertBefore(el("div", "notice", "Some older messages were left out so the conversation fits in the model's context window."), live.view.partsBox);
        break;
      case "error":
        m.error = ev.message;
        break;
      case "done": {
        typing.remove();
        const saved = ev.message;
        if (state.chat) state.chat.messages.push(saved);
        const fresh = assistantView(saved, true);
        live.view.root.replaceWith(fresh.root);
        live.view = fresh;
        scrollToBottom(false);
        break;
      }
    }
  }

  function scheduleFlush(live) {
    if (live._raf) return;
    live._raf = requestAnimationFrame(() => {
      live._raf = null;
      flushLive(live, false);
    });
  }

  /** Bring the streamed message's DOM up to date with live.message.parts. */
  function flushLive(live, final) {
    const v = live.view;
    if (!v.partsBox) return;
    live.message.parts.forEach((p, i) => {
      let node = v.partEls[i];
      if (!node) {
        delete p._new;
        node = addPartEl(v, p, final);
        return;
      }
      if (p.type === "text") MD.render(node, p.text, { final, sources: live.message.sources });
      else if (p.type === "thinking") updateThinking(node, p, final);
      else if (p.type === "tool" && p._dirty) {
        delete p._dirty;
        updateTool(node, p);
      }
    });
    scrollToBottom(false);
  }

  async function stopGenerating() {
    if (!state.busy) return;
    if (state.chatId) {
      try {
        await api(API + "/chats/" + state.chatId + "/stop", { json: {} });
        return;
      } catch (e) { /* fall through to abort */ }
    }
    if (state.abort) state.abort.abort();
  }

  let sidebarTimer = null;
  function refreshSidebarSoon() {
    clearTimeout(sidebarTimer);
    sidebarTimer = setTimeout(loadChats, 300);
  }

  // ----- attachments ------------------------------------------------------------------------------
  function addFiles(fileList) {
    Array.from(fileList).forEach((file) => {
      const item = { key: Math.random().toString(36).slice(2), name: file.name || "pasted-image.png", status: "uploading", meta: null, url: null };
      if (/^image\//.test(file.type)) item.url = URL.createObjectURL(file);
      state.pending.push(item);
      upload(file, item);
    });
    renderPending();
  }

  async function upload(file, item) {
    try {
      const res = await fetch(API + "/upload", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(item.name) },
        body: file,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
      item.meta = data;
      item.status = "ready";
    } catch (e) {
      item.status = "error";
      item.error = e.message;
    }
    renderPending();
  }

  function renderPending() {
    syncPrivate();
    const box = $("#attachments");
    box.innerHTML = "";
    box.hidden = state.pending.length === 0;
    state.pending.forEach((p) => {
      const chip = el("div", "pending-chip " + p.status);
      if (p.url) {
        const img = el("img");
        img.src = p.url;
        img.alt = "";
        chip.appendChild(img);
      } else {
        const ic = el("span", "pc-icon");
        ic.innerHTML = ICON.file;
        chip.appendChild(ic);
      }
      const t = el("div", "pc-text");
      t.appendChild(el("div", "pc-name", p.name));
      let meta = "Uploading…";
      if (p.status === "ready") {
        meta = p.meta.kind === "image" ? "Image" : (p.meta.chars || 0).toLocaleString() + " characters";
        if (p.meta.note) meta += " · " + p.meta.note;
      } else if (p.status === "error") meta = p.error;
      const metaEl = el("div", "pc-meta", meta);
      metaEl.title = meta;
      t.appendChild(metaEl);
      chip.appendChild(t);
      const x = el("button", "pc-remove");
      x.type = "button";
      x.innerHTML = ICON.x;
      x.title = "Remove";
      x.onclick = () => {
        state.pending = state.pending.filter((q) => q !== p);
        if (p.url) URL.revokeObjectURL(p.url);
        renderPending();
      };
      chip.appendChild(x);
      box.appendChild(chip);
    });
    updateSendEnabled();
  }

  // ----- composer --------------------------------------------------------------------------------
  function autoGrow() {
    const ta = $("#input");
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, window.innerHeight * 0.4) + "px";
    updateSendEnabled();
  }

  function scrollToBottom(force) {
    const sc = $("#scroller");
    if (force || state.stickBottom) sc.scrollTop = sc.scrollHeight;
  }

  // ----- sidebar visibility -----------------------------------------------------------------------
  const narrow = () => window.matchMedia("(max-width: 860px)").matches;
  function setSidebar(open) {
    $("#app").classList.toggle("sidebar-closed", !open);
    $("#scrim").hidden = !(open && narrow());
    try { if (!narrow()) localStorage.setItem("lc-sidebar", open ? "1" : "0"); } catch (e) { /* private mode */ }
  }
  function closeSidebarIfOverlay() {
    if (narrow()) setSidebar(false);
  }

  // ----- wiring ----------------------------------------------------------------------------------
  function bind() {
    const input = $("#input");
    input.addEventListener("input", autoGrow);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        if (!state.busy) send();
      }
    });
    input.addEventListener("paste", (e) => {
      const files = Array.from(e.clipboardData.files || []);
      if (files.length) {
        e.preventDefault();
        addFiles(files);
        return;
      }
      const text = e.clipboardData.getData("text/plain");
      if (text && text.length > 8000) {   // very long paste -> attach it as a file, like Claude does
        e.preventDefault();
        addFiles([new File([text], "Pasted text.txt", { type: "text/plain" })]);
        toast("Long text was attached as a file");
      }
    });
    $("#composer").addEventListener("submit", (e) => {
      e.preventDefault();
      if (state.busy) stopGenerating();
      else send();
    });
    $("#attach-btn").onclick = () => $("#file-input").click();
    $("#file-input").onchange = (e) => {
      addFiles(e.target.files);
      e.target.value = "";
    };
    $("#web-toggle").onclick = async (e) => {
      const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
      state.settings.web_search = on;
      syncToggles();
      saveSettings({ web_search: on }).catch(() => {});
    };
    $("#db-toggle").onclick = async (e) => {
      const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
      state.dbOn = on;            // per chat: database data is private, so it is not switched on everywhere
      syncToggles();
      syncPrivate();
    };
    $("#private-toggle").onclick = (e) => {
      const local = e.currentTarget.getAttribute("aria-pressed") === "true";
      if (local && !confirm("Allow cloud (API) models to read the files and database data in this chat? They leave your computer.")) return;
      if (state.chat) state.chat.allow_cloud = local;
      else state.allowCloudNew = local;
      syncPrivate();
    };
    $("#medtech-toggle").onclick = async (e) => {
      const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
      state.settings.medtech = on;
      syncToggles();
      saveSettings({ medtech: on }).catch(() => {});
    };
    $("#think-toggle").onclick = async (e) => {
      const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
      state.settings.think = on;
      syncToggles();
      saveSettings({ think: on }).catch(() => {});
    };
    $("#model-select").onchange = async (e) => {
      await saveSettings({ model: e.target.value }).catch((err) => toast(err.message));
      refreshStatus();
    };
    $("#new-chat").onclick = newChat;
    $("#settings-btn").onclick = openSettings;
    document.querySelectorAll("#settings [data-close]").forEach((b) => (b.onclick = () => $("#settings").close("cancel")));
    $("#settings").addEventListener("close", () => {
      if ($("#settings").returnValue === "save") submitSettings();
    });
    $("#chat-filter").addEventListener("input", renderChatList);
    $("#sidebar-close").onclick = () => setSidebar(false);
    $("#sidebar-open").onclick = () => setSidebar(true);
    $("#scrim").onclick = () => setSidebar(false);
    window.addEventListener("hashchange", route);

    const sc = $("#scroller");
    sc.addEventListener("scroll", () => {
      const near = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
      state.stickBottom = near;
      $("#jump").hidden = near;
    });
    $("#jump").onclick = () => {
      state.stickBottom = true;
      scrollToBottom(true);
    };

    // code-block copy buttons (event delegation)
    $("#messages").addEventListener("click", (e) => {
      const b = e.target.closest(".copy-code");
      if (b) {
        b.dataset.label = "1";
        copyText(b.closest(".codeblock").querySelector("code").textContent, b);
      }
    });

    // drag and drop anywhere on the page
    let depth = 0;
    window.addEventListener("dragenter", (e) => {
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes("Files")) return;
      depth++;
      $("#drop-overlay").hidden = false;
    });
    window.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (!depth) $("#drop-overlay").hidden = true;
    });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      depth = 0;
      $("#drop-overlay").hidden = true;
      if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });

    document.addEventListener("keydown", (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (e.key === "Escape" && state.busy && !$("#settings").open) {
        e.preventDefault();
        stopGenerating();
      } else if (mod && e.shiftKey && (e.key === "o" || e.key === "O")) {
        e.preventDefault();
        newChat();
      } else if (mod && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setSidebar(true);
        $("#chat-filter").focus();
      } else if (e.key === "/" && document.activeElement === document.body) {
        e.preventDefault();
        input.focus();
      }
    });
    window.addEventListener("focus", () => { if (!state.status || !state.status.ollama || !state.status.model_installed) refreshStatus(); });
    window.matchMedia("(max-width: 860px)").addEventListener("change", (e) => setSidebar(!e.matches));
  }

  async function init() {
    if (window.self !== window.top) document.documentElement.classList.add("embedded");
    let open = !narrow();
    try { if (!narrow() && localStorage.getItem("lc-sidebar") === "0") open = false; } catch (e) { /* ignore */ }
    setSidebar(open);
    bind();
    setGreeting();
    try {
      await loadSettings();
    } catch (e) {
      toast("Can't reach the MedScout server. Is `python run.py ui` still running?", 8000);
    }
    syncToggles();
    await Promise.all([refreshStatus(), loadChats().catch(() => {})]);
    route();
    autoGrow();
    $("#input").focus();
    setInterval(() => { if (!state.busy && (!state.status || !state.status.ollama)) refreshStatus(); }, 15000);
  }

  init();
})();
