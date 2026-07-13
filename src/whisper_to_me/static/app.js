"use strict";

/* whisper-to-me — local web UI. Same-origin only; no external requests. */

(function () {
  const state = {
    status: { state: "idle", title: null, started: null, elapsed_s: null },
    notes: [],
    currentNote: null, // note name currently shown in the note view
    currentNoteMd: null, // its raw markdown (edit / copy / checkbox sync)
    editing: false,
    searchResults: null, // null = no active search; [] = search with no hits
    viewArchived: false, // sidebar showing the Archive folder instead of notes
    archived: [], // archived notes list (name/title/modified)
    chatHistory: [], // [{role, content}] for the chat view, in-memory only
    view: "empty", // "empty" | "transcript" | "note" | "chat"
    autoScroll: true,
    daemonUp: false,
    wsRetryMs: 1000,
  };

  const el = {
    daemonDown: document.getElementById("daemon-down"),
    app: document.getElementById("app"),
    statusDot: document.getElementById("status-dot"),
    statusText: document.getElementById("status-text"),
    elapsed: document.getElementById("elapsed"),
    titleInput: document.getElementById("title-input"),
    templateSelect: document.getElementById("template-select"),
    recordBtn: document.getElementById("record-btn"),
    recordBtnLabel: document.getElementById("record-btn-label"),
    emptyRecordBtn: document.getElementById("empty-record-btn"),
    watchBtn: document.getElementById("watch-btn"),
    meetingPrompt: document.getElementById("meeting-prompt"),
    meetingPromptTitle: document.getElementById("meeting-prompt-title"),
    meetingRecordBtn: document.getElementById("meeting-record-btn"),
    meetingIgnoreBtn: document.getElementById("meeting-ignore-btn"),
    notesList: document.getElementById("notes-list"),
    searchInput: document.getElementById("search-input"),
    sidebarSearch: document.querySelector(".sidebar-search"),
    notesTab: document.getElementById("notes-tab"),
    archivedTab: document.getElementById("archived-tab"),
    askBtn: document.getElementById("ask-btn"),
    settingsBtn: document.getElementById("settings-btn"),
    settingsModal: document.getElementById("settings-modal"),
    settingsClose: document.getElementById("settings-close"),
    obsidianVault: document.getElementById("obsidian-vault"),
    obsidianConnect: document.getElementById("obsidian-connect"),
    obsidianDisconnect: document.getElementById("obsidian-disconnect"),
    notionToken: document.getElementById("notion-token"),
    notionDatabase: document.getElementById("notion-database"),
    notionConnect: document.getElementById("notion-connect"),
    notionDisconnect: document.getElementById("notion-disconnect"),
    chatView: document.getElementById("chat-view"),
    chatMessages: document.getElementById("chat-messages"),
    chatForm: document.getElementById("chat-form"),
    chatInput: document.getElementById("chat-input"),
    emptyState: document.getElementById("empty-state"),
    livePane: document.getElementById("live-pane"),
    transcript: document.getElementById("transcript"),
    scratchpad: document.getElementById("scratchpad"),
    noteContainer: document.getElementById("note-container"),
    noteView: document.getElementById("note-view"),
    noteEditor: document.getElementById("note-editor"),
    editorSplit: document.getElementById("editor-split"),
    editorPreview: document.getElementById("editor-preview"),
    viewActions: document.getElementById("view-actions"),
    editActions: document.getElementById("edit-actions"),
    editBtn: document.getElementById("edit-btn"),
    copyBtn: document.getElementById("copy-btn"),
    exportBtn: document.getElementById("export-btn"),
    exportMenu: document.getElementById("export-menu"),
    slackCopyBtn: document.getElementById("slack-copy-btn"),
    htmlBtn: document.getElementById("html-btn"),
    pdfBtn: document.getElementById("pdf-btn"),
    followupBtn: document.getElementById("followup-btn"),
    vaultBtn: document.getElementById("vault-btn"),
    notionBtn: document.getElementById("notion-btn"),
    draftModal: document.getElementById("draft-modal"),
    draftText: document.getElementById("draft-text"),
    draftClose: document.getElementById("draft-close"),
    draftCopy: document.getElementById("draft-copy"),
    saveBtn: document.getElementById("save-btn"),
    cancelBtn: document.getElementById("cancel-btn"),
    toasts: document.getElementById("toasts"),
    confirmModal: document.getElementById("confirm-modal"),
    confirmMessage: document.getElementById("confirm-message"),
    confirmCancel: document.getElementById("confirm-cancel"),
    confirmOk: document.getElementById("confirm-ok"),
  };

  // ---------- inline icons (feather-style SVG paths; no external assets) ----------
  // Static, developer-authored markup — never user/note content — so building
  // each icon by setting innerHTML on a namespaced <svg> is safe.

  const ICONS = {
    archive:
      '<polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>',
    restore:
      '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    trash:
      '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    clipboard:
      '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>',
  };

  function makeIcon(name, extraClass) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "icon" + (extraClass ? " " + extraClass : ""));
    svg.innerHTML = ICONS[name];
    return svg;
  }

  // ---------- small utils ----------

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
  }

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function formatElapsed(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return h > 0 ? `${h}:${pad2(m)}:${pad2(sec)}` : `${m}:${pad2(sec)}`;
  }

  function formatRelativeDate(iso) {
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return "";
    const diffMs = Date.now() - then.getTime();
    const diffDay = Math.floor(diffMs / 86400000);
    if (diffDay <= 0) {
      return then.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
    if (diffDay === 1) return "yesterday";
    if (diffDay < 7) return `${diffDay} days ago`;
    return then.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function toast(message, kind) {
    const node = document.createElement("div");
    node.className = "toast" + (kind === "error" ? " toast-error" : "");
    node.textContent = message;
    el.toasts.appendChild(node);
    requestAnimationFrame(() => node.classList.add("show"));
    setTimeout(() => {
      node.classList.remove("show");
      setTimeout(() => node.remove(), 250);
    }, 4000);
  }

  // In-app replacement for window.confirm(): the desktop shell's embedded
  // webview doesn't render native JS confirm dialogs, so confirm() silently
  // resolves falsy with no visible prompt. Resolves true/false like confirm().
  let confirmResolve = null;
  function closeConfirm(result) {
    el.confirmModal.hidden = true;
    if (confirmResolve) {
      const resolve = confirmResolve;
      confirmResolve = null;
      resolve(result);
    }
  }
  function confirmDialog(message, { danger = false } = {}) {
    el.confirmMessage.textContent = message;
    el.confirmOk.classList.toggle("btn-danger", danger);
    el.confirmModal.hidden = false;
    el.confirmOk.focus();
    return new Promise((resolve) => {
      confirmResolve = resolve;
    });
  }
  el.confirmCancel.addEventListener("click", () => closeConfirm(false));
  el.confirmOk.addEventListener("click", () => closeConfirm(true));
  el.confirmModal.addEventListener("click", (evt) => {
    if (evt.target === el.confirmModal) closeConfirm(false); // backdrop click
  });
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape" && !el.confirmModal.hidden) closeConfirm(false);
  });

  // ---------- markdown rendering (vendored markdown-it, no CDN) ----------
  // html:false keeps raw HTML in notes escaped — content comes from speech
  // and a local LLM, so it is never trusted as markup. breaks:true gives the
  // single-newline transcript lines their own visual lines.

  const md = window
    .markdownit({ html: false, linkify: false, breaks: true })
    .use(window.markdownitTaskLists, { enabled: true });

  const STAMP_RE = /^\[(\d+:\d{2}:\d{2})\]$/;

  // Notes carry YAML frontmatter for Obsidian; it is metadata, not prose —
  // hide it from the rendered view (Edit/Copy still see the raw markdown).
  function stripFrontmatter(mdText) {
    if (!mdText.startsWith("---\n")) return mdText;
    const end = mdText.indexOf("\n---\n", 4);
    if (end === -1) return mdText;
    return mdText.slice(end + 5).replace(/^\n+/, "");
  }

  function renderNote(mdText) {
    el.noteView.innerHTML = md.render(stripFrontmatter(mdText));
    enhanceCheckboxes();
    foldTranscript();
    anchorStamps();
  }

  // Checkbox order in the DOM mirrors task-line order in the file (the
  // server's _TASK_RE matches exactly what the task-lists plugin renders),
  // so the nth checkbox toggles the nth task line.
  function enhanceCheckboxes() {
    const boxes = el.noteView.querySelectorAll("input.task-list-item-checkbox");
    boxes.forEach((box, index) => {
      box.addEventListener("change", async () => {
        const resp = await fetch(`/api/notes/${encodeURIComponent(state.currentNote)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task_index: index, checked: box.checked }),
        }).catch(() => null);
        if (!resp || !resp.ok) {
          box.checked = !box.checked;
          toast(
            resp && resp.status === 409
              ? "That note is still being recorded."
              : "Could not update the task.",
            "error"
          );
          return;
        }
        // Keep the raw markdown in sync so Edit/Copy see the new state.
        try {
          state.currentNoteMd = await fetchNoteContent(state.currentNote);
        } catch (err) {
          /* next openNote refetches anyway */
        }
      });
    });
  }

  // Long transcripts drown the summary: collapse the "## Transcript"
  // section into a <details> fold, closed by default.
  function foldTranscript() {
    for (const h2 of el.noteView.querySelectorAll("h2")) {
      if (h2.textContent.trim() !== "Transcript") continue;
      const section = [];
      for (let node = h2.nextSibling; node; node = node.nextSibling) {
        if (node.nodeType === 1 && /^H[12]$/.test(node.tagName)) break;
        section.push(node);
      }
      const details = document.createElement("details");
      details.className = "transcript-fold";
      const summary = document.createElement("summary");
      summary.textContent = "Transcript";
      details.appendChild(summary);
      h2.replaceWith(details);
      section.forEach((node) => details.appendChild(node));
      break;
    }
  }

  // Transcript stamps (**[0:03:12]**) become anchor targets; a [0:03:12]
  // reference anywhere else in the note becomes a link that opens the fold
  // and flashes that line.
  function anchorStamps() {
    const fold = el.noteView.querySelector(".transcript-fold");
    if (!fold) return;
    const ids = new Map(); // stamp text -> first anchor id
    fold.querySelectorAll("strong").forEach((strong) => {
      const m = strong.textContent.match(STAMP_RE);
      if (!m) return;
      strong.classList.add("md-stamp");
      if (!ids.has(m[1])) {
        strong.id = `t-${m[1].replace(/:/g, "-")}`;
        ids.set(m[1], strong.id);
      }
    });
    if (ids.size === 0) return;
    el.noteView.querySelectorAll("p, li").forEach((node) => {
      if (fold.contains(node)) return;
      for (const child of [...node.childNodes]) {
        if (child.nodeType !== 3) continue; // text nodes only
        const parts = child.textContent.split(/\[(\d+:\d{2}:\d{2})\]/);
        if (parts.length < 3) continue;
        const frag = document.createDocumentFragment();
        parts.forEach((part, i) => {
          if (i % 2 === 0) {
            if (part) frag.appendChild(document.createTextNode(part));
            return;
          }
          const id = ids.get(part);
          if (!id) {
            frag.appendChild(document.createTextNode(`[${part}]`));
            return;
          }
          const link = document.createElement("a");
          link.className = "stamp-link";
          link.href = `#${id}`;
          link.textContent = `[${part}]`;
          link.addEventListener("click", (evt) => {
            evt.preventDefault();
            const target = document.getElementById(id);
            if (!target) return;
            fold.open = true;
            target.scrollIntoView({ behavior: "smooth", block: "center" });
            target.classList.remove("flash");
            requestAnimationFrame(() => target.classList.add("flash"));
          });
          frag.appendChild(link);
        });
        child.replaceWith(frag);
      }
    });
  }

  // ---------- API ----------

  async function apiGet(path) {
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`${path} -> ${resp.status}`);
    return resp;
  }

  async function apiPost(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  async function fetchStatus() {
    const resp = await apiGet("/api/status");
    return resp.json();
  }

  async function fetchNotes() {
    const resp = await apiGet("/api/notes");
    return resp.json();
  }

  async function fetchNoteContent(name) {
    const resp = await apiGet(`/api/notes/${encodeURIComponent(name)}`);
    return resp.text();
  }

  async function loadTemplates() {
    try {
      const list = await (await apiGet("/api/templates")).json();
      for (const t of list) {
        const opt = document.createElement("option");
        opt.value = t.name;
        opt.textContent = t.description || t.name;
        el.templateSelect.appendChild(opt);
      }
    } catch (err) {
      /* non-critical: the Auto option alone still works */
    }
  }

  // ---------- view switching ----------

  function setView(view) {
    state.view = view;
    el.emptyState.hidden = view !== "empty";
    el.livePane.hidden = view !== "transcript";
    el.noteContainer.hidden = view !== "note";
    el.chatView.hidden = view !== "chat";
  }

  // ---------- session bar ----------

  // True between a record start/stop click and the daemon's next status
  // event: the button disables immediately so the click visibly "took".
  let recordPending = false;

  function updateSessionBar() {
    const s = state.status;
    el.statusDot.className = "status-dot status-" + s.state;

    const labels = {
      idle: "Ready",
      starting: "Starting the recorder…",
      recording: s.title || "Recording",
      stopping: "Finishing — transcribing the last audio…",
      watching: "Watching for meetings…",
      prompting: s.title ? `Meeting detected — ${s.title}` : "Meeting detected",
      summarizing: "Summarizing…",
    };
    el.statusText.textContent = labels[s.state] || s.state;

    // The Notion-style popup: visible exactly while the daemon holds a
    // detected meeting open for a record/ignore decision.
    el.meetingPrompt.hidden = s.state !== "prompting";
    if (s.state === "prompting") {
      el.meetingPromptTitle.textContent = s.title || "Meeting";
    }

    if (s.state === "recording" && s.started) {
      const startedMs = Date.parse(s.started);
      el.elapsed.textContent = Number.isNaN(startedMs)
        ? ""
        : formatElapsed((Date.now() - startedMs) / 1000);
    } else {
      el.elapsed.textContent = "";
    }

    const busy = s.state === "stopping" || s.state === "summarizing";
    // While watching, "New meeting" stays live: the daemon preempts the idle
    // watch for a manual recording (and resumes watching afterwards).
    el.recordBtn.disabled = busy || s.state === "prompting" || recordPending;
    el.watchBtn.disabled =
      s.state !== "idle" && s.state !== "watching" && s.state !== "prompting";
    const settable = s.state === "idle" || s.state === "watching";
    el.titleInput.disabled = !settable;
    el.templateSelect.disabled = !settable;

    // The record button doubles as the stop button while a session runs
    // ("starting" included, so a misclick can be cancelled immediately).
    const asStop = s.state === "recording" || s.state === "starting";
    el.recordBtn.classList.toggle("is-stop", asStop);
    el.recordBtn.classList.toggle("is-busy", busy || recordPending);
    el.recordBtnLabel.textContent = busy
      ? (s.state === "stopping" ? "Finishing…" : "Summarizing…")
      : asStop
        ? "Stop"
        : "New meeting";

    if (s.state === "watching" || s.state === "prompting") {
      el.watchBtn.textContent = "Stop Watching";
      el.watchBtn.classList.add("is-active");
    } else {
      el.watchBtn.textContent = "Watch";
      el.watchBtn.classList.remove("is-active");
    }

    renderNotesList(); // the "Live session" entry depends on session state
    if (state.view === "empty" && s.state !== "idle" && state.currentNote === null) {
      setView("transcript");
    }
  }

  let elapsedTimer = null;
  function startElapsedTicker() {
    if (elapsedTimer) return;
    elapsedTimer = setInterval(() => {
      if (state.status.state === "recording") updateSessionBar();
    }, 500);
  }

  // ---------- notes sidebar ----------

  // A small round icon button used for the per-note sidebar actions.
  function actionButton(iconName, label, danger, onClick) {
    const btn = document.createElement("button");
    btn.className = "note-action" + (danger ? " note-action-danger" : "");
    btn.title = label;
    btn.setAttribute("aria-label", label);
    btn.appendChild(makeIcon(iconName));
    btn.addEventListener("click", (evt) => {
      evt.stopPropagation(); // never trigger the row's openNote
      onClick();
    });
    return btn;
  }

  function renderArchivedList() {
    if (state.archived.length === 0) {
      const empty = document.createElement("div");
      empty.className = "notes-empty";
      empty.textContent = "No archived notes.";
      el.notesList.appendChild(empty);
      return;
    }
    for (const note of state.archived) {
      const li = document.createElement("li");
      const row = document.createElement("div");
      row.className = "note-row";

      const item = document.createElement("div");
      item.className = "note-item note-item-static";
      const title = document.createElement("span");
      title.className = "note-title";
      title.textContent = note.title;
      const date = document.createElement("span");
      date.className = "note-date";
      date.textContent = formatRelativeDate(note.modified);
      item.appendChild(title);
      item.appendChild(date);
      row.appendChild(item);

      const actions = document.createElement("div");
      actions.className = "note-actions";
      actions.appendChild(actionButton("restore", "Restore note", false, () => restoreNote(note)));
      actions.appendChild(
        actionButton("trash", "Delete permanently", true, () => deleteArchivedNote(note))
      );
      row.appendChild(actions);

      li.appendChild(row);
      el.notesList.appendChild(li);
    }
  }

  function renderNotesList() {
    el.notesList.innerHTML = "";

    if (state.viewArchived) {
      renderArchivedList();
      return;
    }

    if (state.status.state !== "idle") {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.className = "live-item" + (state.currentNote === null ? " active" : "");
      btn.innerHTML =
        '<span class="status-dot status-' +
        state.status.state +
        '" style="width:7px;height:7px"></span><span>Live session</span>';
      btn.addEventListener("click", () => {
        state.currentNote = null;
        setView("transcript");
        renderNotesList();
      });
      li.appendChild(btn);
      el.notesList.appendChild(li);
    }

    const searching = state.searchResults !== null;
    const entries = searching ? state.searchResults : state.notes;

    if (entries.length === 0) {
      if (searching || state.status.state === "idle") {
        const empty = document.createElement("div");
        empty.className = "notes-empty";
        empty.textContent = searching ? "No matches." : "No notes yet.";
        el.notesList.appendChild(empty);
      }
      return;
    }

    for (const note of entries) {
      const li = document.createElement("li");
      const row = document.createElement("div");
      row.className = "note-row";

      const btn = document.createElement("button");
      btn.className = "note-item" + (state.currentNote === note.name ? " active" : "");
      const title = document.createElement("span");
      title.className = "note-title";
      title.textContent = note.title;
      const date = document.createElement("span");
      date.className = "note-date";
      date.textContent = formatRelativeDate(note.modified);
      btn.appendChild(title);
      btn.appendChild(date);
      if (searching && note.snippet) {
        const snippet = document.createElement("span");
        snippet.className = "note-snippet";
        // Snippets are escaped as plain text first; only the private-use
        // markers the server put around hits become real <mark> tags.
        snippet.innerHTML = escapeHtml(note.snippet)
          .replaceAll("\ue000", "<mark>")
          .replaceAll("\ue001", "</mark>");
        btn.appendChild(snippet);
      }
      btn.addEventListener("click", () => openNote(note.name));
      row.appendChild(btn);

      const actions = document.createElement("div");
      actions.className = "note-actions";
      actions.appendChild(actionButton("archive", "Archive note", false, () => archiveNote(note)));
      actions.appendChild(actionButton("trash", "Delete note", true, () => deleteNote(note)));
      row.appendChild(actions);

      li.appendChild(row);
      el.notesList.appendChild(li);
    }
  }

  // ---------- sidebar tabs (Notes / Archived) + note actions ----------

  function updateArchivedTab() {
    const n = state.archived.length;
    el.archivedTab.textContent = n ? `Archived (${n})` : "Archived";
  }

  async function refreshArchived() {
    try {
      state.archived = await (await apiGet("/api/archived")).json();
    } catch (err) {
      /* non-critical: leave the previous archived list */
    }
    updateArchivedTab();
    if (state.viewArchived) renderNotesList();
  }

  function setSidebarTab(archived) {
    state.viewArchived = archived;
    el.notesTab.classList.toggle("active", !archived);
    el.archivedTab.classList.toggle("active", archived);
    el.sidebarSearch.hidden = archived; // search only spans active notes
    if (archived) {
      refreshArchived();
    } else {
      // Clear any in-progress search so the notes list shows in full.
      el.searchInput.value = "";
      state.searchResults = null;
      renderNotesList();
    }
  }

  el.notesTab.addEventListener("click", () => setSidebarTab(false));
  el.archivedTab.addEventListener("click", () => setSidebarTab(true));

  // If the note currently open was archived/deleted, clear the reading pane.
  function forgetOpenNote(name) {
    if (state.currentNote !== name) return;
    state.currentNote = null;
    state.currentNoteMd = null;
    exitEditMode();
    setView("empty");
  }

  async function archiveNote(note) {
    const resp = await apiPost(`/api/notes/${encodeURIComponent(note.name)}/archive`);
    if (!resp.ok) {
      toast(
        resp.status === 409 ? "That note is still being recorded." : "Could not archive the note.",
        "error"
      );
      return;
    }
    forgetOpenNote(note.name);
    refreshNotes();
    refreshArchived();
    toast(`Archived \u201c${note.title}\u201d`);
  }

  async function deleteNote(note) {
    const ok = await confirmDialog(
      `Delete \u201c${note.title}\u201d?\n\nThis permanently removes the note file.`,
      { danger: true }
    );
    if (!ok) return;
    const resp = await fetch(`/api/notes/${encodeURIComponent(note.name)}`, {
      method: "DELETE",
    }).catch(() => null);
    if (!resp || !resp.ok) {
      toast(
        resp && resp.status === 409
          ? "That note is still being recorded."
          : "Could not delete the note.",
        "error"
      );
      return;
    }
    forgetOpenNote(note.name);
    refreshNotes();
    toast(`Deleted \u201c${note.title}\u201d`);
  }

  async function restoreNote(note) {
    const resp = await apiPost(`/api/archived/${encodeURIComponent(note.name)}/restore`);
    if (!resp.ok) {
      toast("Could not restore the note.", "error");
      return;
    }
    refreshArchived();
    refreshNotes(); // it reappears in the Notes tab
    toast(`Restored \u201c${note.title}\u201d`);
  }

  async function deleteArchivedNote(note) {
    const ok = await confirmDialog(`Delete \u201c${note.title}\u201d permanently?`, { danger: true });
    if (!ok) return;
    const resp = await fetch(`/api/archived/${encodeURIComponent(note.name)}`, {
      method: "DELETE",
    }).catch(() => null);
    if (!resp || !resp.ok) {
      toast("Could not delete the note.", "error");
      return;
    }
    refreshArchived();
    toast(`Deleted \u201c${note.title}\u201d`);
  }

  // ---------- sidebar search ----------

  let searchTimer = null;
  let searchSeq = 0; // late responses from stale queries must not win

  async function runSearch() {
    const q = el.searchInput.value.trim();
    const seq = ++searchSeq;
    if (!q) {
      state.searchResults = null;
      renderNotesList();
      return;
    }
    let results = [];
    try {
      const resp = await apiGet(`/api/search?q=${encodeURIComponent(q)}`);
      results = await resp.json();
    } catch (err) {
      /* daemon hiccup: show "No matches." rather than a stale list */
    }
    if (seq !== searchSeq) return;
    state.searchResults = results;
    renderNotesList();
  }

  el.searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 200);
  });
  el.searchInput.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") {
      el.searchInput.value = "";
      clearTimeout(searchTimer);
      runSearch();
    }
  });

  async function refreshNotes() {
    try {
      state.notes = await fetchNotes();
      renderNotesList();
    } catch (err) {
      // Notes list is non-critical; leave the previous list showing.
    }
  }

  function editorDirty() {
    return state.editing && el.noteEditor.value !== state.currentNoteMd;
  }

  async function openNote(name) {
    if (editorDirty() && !(await confirmDialog("Discard your unsaved edits?"))) return;
    try {
      const mdText = await fetchNoteContent(name);
      state.currentNote = name;
      state.currentNoteMd = mdText;
      exitEditMode();
      renderNote(mdText);
      setView("note");
      renderNotesList();
    } catch (err) {
      toast("Could not load that note.", "error");
    }
  }

  // ---------- note toolbar: edit / copy ----------

  function enterEditMode() {
    state.editing = true;
    el.noteEditor.value = state.currentNoteMd || "";
    el.noteView.hidden = true;
    el.editorSplit.hidden = false;
    el.noteContainer.classList.add("editing"); // widen: editor + live preview
    el.viewActions.hidden = true;
    el.editActions.hidden = false;
    renderEditorPreview();
    el.noteEditor.focus();
  }

  function exitEditMode() {
    state.editing = false;
    el.editorSplit.hidden = true;
    el.noteContainer.classList.remove("editing");
    el.noteView.hidden = false;
    el.viewActions.hidden = false;
    el.editActions.hidden = true;
  }

  // ---------- markdown editor: live preview + Notion-style typing ----------

  // The preview reuses the same (html:false) markdown-it renderer as the note
  // view, so what you see while typing is exactly what Save will show.
  function renderEditorPreview() {
    el.editorPreview.innerHTML = md.render(stripFrontmatter(el.noteEditor.value));
  }

  let previewTimer = null;
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderEditorPreview, 150);
  }

  el.noteEditor.addEventListener("input", schedulePreview);

  // Insert text at the caret, keeping the browser's undo stack when possible
  // (execCommand is deprecated but still the only undo-preserving path).
  function editorInsert(text) {
    const ta = el.noteEditor;
    let done = false;
    try {
      done = text
        ? document.execCommand("insertText", false, text)
        : document.execCommand("delete", false);
    } catch (err) {
      done = false;
    }
    if (!done) {
      ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
    }
    schedulePreview();
  }

  // "- ", "* ", "1. ", "- [ ] "… — the prefixes the editor auto-continues.
  const LIST_PREFIX_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?/;

  function currentLineBounds(ta) {
    const start = ta.value.lastIndexOf("\n", ta.selectionStart - 1) + 1;
    const nl = ta.value.indexOf("\n", ta.selectionStart);
    return [start, nl === -1 ? ta.value.length : nl];
  }

  el.noteEditor.addEventListener("keydown", (evt) => {
    const ta = el.noteEditor;

    // Cmd/Ctrl+B / I: wrap the selection in **bold** / *italics*.
    if ((evt.metaKey || evt.ctrlKey) && (evt.key === "b" || evt.key === "i")) {
      evt.preventDefault();
      const wrap = evt.key === "b" ? "**" : "*";
      const start = ta.selectionStart;
      const sel = ta.value.slice(start, ta.selectionEnd);
      editorInsert(wrap + sel + wrap);
      ta.selectionStart = start + wrap.length;
      ta.selectionEnd = start + wrap.length + sel.length;
      return;
    }

    if (evt.metaKey || evt.ctrlKey || evt.altKey) return;

    // Enter continues lists and task items; Enter on an empty item ends the
    // list (both as in Notion/Obsidian).
    if (evt.key === "Enter" && !evt.shiftKey && ta.selectionStart === ta.selectionEnd) {
      const [lineStart] = currentLineBounds(ta);
      const line = ta.value.slice(lineStart, ta.selectionStart);
      const m = line.match(LIST_PREFIX_RE);
      if (!m) return;
      evt.preventDefault();
      if (!line.slice(m[0].length)) {
        ta.selectionStart = lineStart; // empty item: remove the marker
        editorInsert("");
        return;
      }
      let marker = m[2];
      const num = marker.match(/^(\d+)([.)])$/);
      if (num) marker = `${Number(num[1]) + 1}${num[2]}`;
      editorInsert("\n" + m[1] + marker + m[3] + (m[4] ? "[ ] " : ""));
      return;
    }

    // Tab / Shift+Tab indent and outdent list items.
    if (evt.key === "Tab") {
      evt.preventDefault();
      const caret = ta.selectionStart;
      const [lineStart, lineEnd] = currentLineBounds(ta);
      const line = ta.value.slice(lineStart, lineEnd);
      if (LIST_PREFIX_RE.test(line)) {
        if (evt.shiftKey) {
          const removed = Math.min(2, (line.match(/^ */) || [""])[0].length);
          if (removed === 0) return;
          ta.selectionStart = lineStart;
          ta.selectionEnd = lineStart + removed;
          editorInsert("");
          const pos = Math.max(lineStart, caret - removed);
          ta.selectionStart = ta.selectionEnd = pos;
        } else {
          ta.selectionStart = ta.selectionEnd = lineStart;
          editorInsert("  ");
          ta.selectionStart = ta.selectionEnd = caret + 2;
        }
      } else if (!evt.shiftKey) {
        editorInsert("  ");
      }
    }
  });

  async function saveEdit() {
    const content = el.noteEditor.value;
    const resp = await fetch(`/api/notes/${encodeURIComponent(state.currentNote)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).catch(() => null);
    if (!resp || !resp.ok) {
      toast(
        resp && resp.status === 409
          ? "That note is still being recorded."
          : "Could not save the note.",
        "error"
      );
      return;
    }
    state.currentNoteMd = content;
    exitEditMode();
    renderNote(content);
    refreshNotes(); // an edited H1 changes the sidebar title
    toast("Saved.");
  }

  el.editBtn.addEventListener("click", enterEditMode);
  el.cancelBtn.addEventListener("click", async () => {
    if (editorDirty() && !(await confirmDialog("Discard your unsaved edits?"))) return;
    exitEditMode();
  });
  el.saveBtn.addEventListener("click", saveEdit);
  el.copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.currentNoteMd || "");
      toast("Copied as Markdown.");
    } catch (err) {
      toast("Could not copy to the clipboard.", "error");
    }
  });

  // ---------- export menu (all local, except the explicit Notion push) ----------

  function closeExportMenu() {
    el.exportMenu.hidden = true;
  }

  el.exportBtn.addEventListener("click", async (evt) => {
    evt.stopPropagation();
    if (!el.exportMenu.hidden) {
      closeExportMenu();
      return;
    }
    // Config is read per open so a freshly edited config.toml shows up
    // without reloading the page (the daemon re-reads it per request too).
    try {
      const cfg = await (await apiGet("/api/export/config")).json();
      el.vaultBtn.hidden = !cfg.obsidian_vault;
      el.notionBtn.hidden = !cfg.notion_configured;
    } catch (err) {
      el.vaultBtn.hidden = true;
      el.notionBtn.hidden = true;
    }
    el.exportMenu.hidden = false;
  });

  document.addEventListener("click", (evt) => {
    if (!el.exportMenu.hidden && !el.exportMenu.contains(evt.target)) closeExportMenu();
  });
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") closeExportMenu();
  });

  // Slack flavor: *bold* instead of **bold**, • bullets, no headings syntax.
  // The transcript is dropped — you share the summary, not the raw log.
  function slackText(mdText) {
    let text = stripFrontmatter(mdText);
    const cut = text.search(/^## Transcript$/m);
    if (cut !== -1) text = text.slice(0, cut);
    return text
      .replace(/^#{1,6}\s+(.*)$/gm, "*$1*")
      .replace(/^\s*[-*+]\s+\[[xX]\]\s+/gm, "• ☑ ")
      .replace(/^\s*[-*+]\s+\[ \]\s+/gm, "• ☐ ")
      .replace(/^\s*[-*+]\s+/gm, "• ")
      .replace(/\*\*([^*]+)\*\*/g, "*$1*")
      .replace(/^---\s*$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  el.slackCopyBtn.addEventListener("click", async () => {
    closeExportMenu();
    try {
      await navigator.clipboard.writeText(slackText(state.currentNoteMd || ""));
      toast("Copied for Slack (summary only).");
    } catch (err) {
      toast("Could not copy to the clipboard.", "error");
    }
  });

  // Standalone HTML file, rendered and styled locally — no external assets.
  const EXPORT_CSS = `
    body { max-width: 46rem; margin: 2rem auto; padding: 0 1rem;
           font: 16px/1.6 -apple-system, "Segoe UI", sans-serif; color: #1f1f1f; }
    h1 { font-size: 1.5rem; } h2 { font-size: 1.15rem; margin-top: 1.6rem; }
    code { font-family: ui-monospace, Menlo, monospace; background: #f2f2f0;
           padding: 0.1em 0.3em; border-radius: 4px; }
    hr { border: 0; border-top: 1px solid #e4e4e2; margin: 1.5rem 0; }
    ul { padding-left: 1.4rem; } li { margin: 0.15rem 0; }
    input[type=checkbox] { margin-right: 0.4em; }
    blockquote { border-left: 3px solid #ddd; margin: 0; padding-left: 1rem; }`;

  el.htmlBtn.addEventListener("click", () => {
    closeExportMenu();
    const body = md.render(stripFrontmatter(state.currentNoteMd || ""));
    const title = escapeHtml(noteTitleFromMd(state.currentNoteMd || "") || state.currentNote);
    const doc = `<!doctype html>\n<html lang="en"><head><meta charset="utf-8">` +
      `<title>${title}</title><style>${EXPORT_CSS}</style></head>\n` +
      `<body>${body}</body></html>\n`;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([doc], { type: "text/html" }));
    link.download = (state.currentNote || "note.md").replace(/\.md$/, ".html");
    link.click();
    URL.revokeObjectURL(link.href);
  });

  function noteTitleFromMd(mdText) {
    const m = stripFrontmatter(mdText).match(/^#\s+(.+)$/m);
    return m ? m[1].trim() : null;
  }

  el.pdfBtn.addEventListener("click", () => {
    closeExportMenu();
    window.print(); // print CSS shows just the note; "Save as PDF" from there
  });

  el.vaultBtn.addEventListener("click", async () => {
    closeExportMenu();
    const resp = await apiPost(`/api/notes/${encodeURIComponent(state.currentNote)}/vault`);
    if (resp.ok) {
      toast("Copied into the Obsidian vault.");
    } else {
      toast(
        resp.status === 409 ? "That note is still being recorded." : "Could not copy to the vault.",
        "error"
      );
    }
  });

  el.notionBtn.addEventListener("click", async () => {
    closeExportMenu();
    const title = noteTitleFromMd(state.currentNoteMd || "") || state.currentNote;
    // The one action that sends data off this machine — spell it out.
    const ok = await confirmDialog(
      `Send this entire note — “${title}” (title, date, attendees, summary and ` +
        `full transcript, exactly as shown) — to your Notion database via ` +
        `api.notion.com?\n\nThis is the only whisper-to-me action that sends ` +
        `anything off this machine. Nothing else is ever uploaded.`
    );
    if (!ok) return;
    toast("Pushing to Notion…");
    const resp = await apiPost(`/api/notes/${encodeURIComponent(state.currentNote)}/notion`);
    if (resp.ok) {
      toast("Pushed to Notion.");
    } else {
      let detail = "Could not push to Notion.";
      try {
        detail = (await resp.json()).detail || detail;
      } catch (err) { /* keep the generic message */ }
      toast(detail, "error");
    }
  });

  // ---------- follow-up email draft (local; opens a copy-me modal) ----------

  function openDraftModal(text) {
    el.draftText.value = text;
    el.draftModal.hidden = false;
  }

  function closeDraftModal() {
    el.draftModal.hidden = true;
  }

  el.followupBtn.addEventListener("click", async () => {
    closeExportMenu();
    if (!state.currentNote) return;
    toast("Drafting a follow-up (local)…");
    const resp = await apiPost(`/api/notes/${encodeURIComponent(state.currentNote)}/followup`);
    if (!resp.ok) {
      let detail = "Could not draft a follow-up.";
      try {
        detail = (await resp.json()).detail || detail;
      } catch (err) {
        /* keep the generic message */
      }
      toast(detail, "error");
      return;
    }
    const data = await resp.json();
    openDraftModal(data.draft || "");
  });

  // Do NOT auto-copy after the fetch — Safari drops the user-gesture context
  // across the await and the write fails silently. The button is a fresh
  // gesture and works everywhere.
  el.draftCopy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(el.draftText.value);
      toast("Copied — nothing was sent anywhere.");
    } catch (err) {
      toast("Could not copy to the clipboard.", "error");
    }
  });

  el.draftClose.addEventListener("click", closeDraftModal);
  el.draftModal.addEventListener("click", (evt) => {
    if (evt.target === el.draftModal) closeDraftModal(); // backdrop click
  });
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape" && !el.draftModal.hidden) closeDraftModal();
  });

  // ---------- transcript ----------

  function clearTranscript() {
    el.transcript.innerHTML = "";
    state.autoScroll = true;
  }

  function maybeAutoScroll() {
    if (state.autoScroll) {
      el.transcript.scrollTop = el.transcript.scrollHeight;
    }
  }

  el.transcript.addEventListener("scroll", () => {
    const distanceFromBottom =
      el.transcript.scrollHeight - el.transcript.scrollTop - el.transcript.clientHeight;
    state.autoScroll = distanceFromBottom < 40;
  });

  function appendTranscriptLine(evt) {
    const row = document.createElement("div");
    row.className = "t-line";

    const stamp = document.createElement("span");
    stamp.className = "t-stamp";
    stamp.textContent = evt.stamp || "";
    row.appendChild(stamp);

    if (evt.speaker) {
      const chip = document.createElement("span");
      chip.className = "chip " + (evt.speaker === "You" ? "chip-you" : "chip-others");
      chip.textContent = evt.speaker;
      row.appendChild(chip);
    }

    const text = document.createElement("span");
    text.className = "t-text";
    text.textContent = evt.text || "";
    row.appendChild(text);

    el.transcript.appendChild(row);
    if (state.view === "transcript") maybeAutoScroll();
  }

  // A "Last time…" brief, pinned at the top of the transcript. Built with
  // textContent (never innerHTML) since title/tldr come from note content.
  // clearTranscript() removes it when a new session starts.
  function showBrief(evt) {
    const card = document.createElement("div");
    card.className = "brief-card";

    const head = document.createElement("div");
    head.className = "brief-head";
    head.appendChild(makeIcon("clipboard"));
    head.appendChild(document.createTextNode("Last time — "));
    const strong = document.createElement("strong");
    strong.textContent = evt.title || "";
    head.appendChild(strong);
    const dismiss = document.createElement("button");
    dismiss.className = "brief-dismiss";
    dismiss.appendChild(makeIcon("close"));
    dismiss.title = "Dismiss";
    dismiss.setAttribute("aria-label", "Dismiss");
    dismiss.addEventListener("click", () => card.remove());
    head.appendChild(dismiss);
    card.appendChild(head);

    if (evt.tldr) {
      const body = document.createElement("div");
      body.className = "brief-tldr";
      body.textContent = evt.tldr;
      card.appendChild(body);
    }
    if (evt.name) {
      const open = document.createElement("a");
      open.className = "brief-open";
      open.href = "#";
      open.textContent = "Open note";
      open.addEventListener("click", (e) => {
        e.preventDefault();
        openNote(evt.name);
      });
      card.appendChild(open);
    }
    el.transcript.insertBefore(card, el.transcript.firstChild);
  }

  function appendTranscriptNotice(message) {
    const row = document.createElement("div");
    row.className = "t-notice";
    row.textContent = message;
    el.transcript.appendChild(row);
    if (state.view === "transcript") maybeAutoScroll();
  }

  // ---------- scratchpad (notes-first: your notes guide the summary) ----------

  let scratchpadTimer = null;
  let scratchpadErrorShown = false;

  async function saveScratchpad() {
    if (state.status.state === "idle") return; // no session to attach notes to
    const resp = await fetch("/api/session/scratchpad", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: el.scratchpad.value }),
    }).catch(() => null);
    if (!resp || !resp.ok) {
      if (!scratchpadErrorShown) {
        toast("Couldn't save your notes to the session.", "error");
        scratchpadErrorShown = true; // one toast, not one per keystroke
      }
      return;
    }
    scratchpadErrorShown = false;
  }

  async function syncScratchpad() {
    if (state.status.state === "idle") return;
    try {
      const data = await (await apiGet("/api/session/scratchpad")).json();
      el.scratchpad.value = data.content || "";
    } catch (err) {
      /* non-critical: leave whatever the textarea has */
    }
  }

  el.scratchpad.addEventListener("input", () => {
    clearTimeout(scratchpadTimer);
    scratchpadTimer = setTimeout(saveScratchpad, 750);
  });

  // ---------- chat with your meetings (local RAG) ----------

  function openChat() {
    state.currentNote = null;
    setView("chat");
    renderNotesList();
    el.chatInput.focus();
  }
  el.askBtn.addEventListener("click", openChat);

  function scrollChat() {
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
  }

  function appendUserMessage(text) {
    const wrap = document.createElement("div");
    wrap.className = "chat-msg chat-user";
    wrap.textContent = text;
    el.chatMessages.appendChild(wrap);
    scrollChat();
  }

  function appendThinking() {
    const wrap = document.createElement("div");
    wrap.className = "chat-msg chat-assistant chat-thinking";
    wrap.textContent = "Thinking…";
    el.chatMessages.appendChild(wrap);
    scrollChat();
    return wrap;
  }

  // Turn [n] markers into links to the cited note. Walks text nodes so it
  // only touches rendered text, never the markdown-it HTML structure.
  function linkifyCitations(root, sources) {
    const byN = new Map(sources.map((s) => [s.n, s]));
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parts = node.textContent.split(/\[(\d+)\]/);
      if (parts.length < 3) continue;
      const frag = document.createDocumentFragment();
      parts.forEach((part, i) => {
        if (i % 2 === 0) {
          if (part) frag.appendChild(document.createTextNode(part));
          return;
        }
        const src = byN.get(Number(part));
        if (!src) {
          frag.appendChild(document.createTextNode(`[${part}]`));
          return;
        }
        const a = document.createElement("a");
        a.className = "cite";
        a.href = "#";
        a.textContent = `[${part}]`;
        a.title = src.title;
        a.addEventListener("click", (evt) => {
          evt.preventDefault();
          openNote(src.name);
        });
        frag.appendChild(a);
      });
      node.replaceWith(frag);
    }
  }

  function renderSourceList(sources) {
    const list = document.createElement("div");
    list.className = "chat-sources";
    sources.forEach((s, i) => {
      if (i) list.appendChild(document.createTextNode(" · "));
      const a = document.createElement("a");
      a.className = "cite";
      a.href = "#";
      a.textContent = `${s.n}. ${s.title}`;
      a.addEventListener("click", (evt) => {
        evt.preventDefault();
        openNote(s.name);
      });
      list.appendChild(a);
    });
    return list;
  }

  function appendAssistantMessage(answer, sources) {
    const wrap = document.createElement("div");
    wrap.className = "chat-msg chat-assistant";
    const body = document.createElement("div");
    body.className = "chat-body";
    body.innerHTML = md.render(answer); // md is html:false, so this is safe
    linkifyCitations(body, sources);
    wrap.appendChild(body);
    if (sources.length) wrap.appendChild(renderSourceList(sources));
    el.chatMessages.appendChild(wrap);
    scrollChat();
  }

  async function askQuestion(question) {
    const history = state.chatHistory.slice(-6); // prior turns only
    appendUserMessage(question);
    state.chatHistory.push({ role: "user", content: question });
    const placeholder = appendThinking();
    el.chatInput.disabled = true;
    let data = null;
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history }),
      });
      if (!resp.ok) throw new Error(`chat -> ${resp.status}`);
      data = await resp.json();
    } catch (err) {
      placeholder.remove();
      state.chatHistory.pop(); // roll back the unanswered turn
      toast("Couldn't get an answer.", "error");
      el.chatInput.disabled = false;
      el.chatInput.focus();
      return;
    }
    placeholder.remove();
    appendAssistantMessage(data.answer, data.sources || []);
    state.chatHistory.push({ role: "assistant", content: data.answer });
    el.chatInput.disabled = false;
    el.chatInput.focus();
  }

  el.chatForm.addEventListener("submit", (evt) => {
    evt.preventDefault();
    const q = el.chatInput.value.trim();
    if (!q || el.chatInput.disabled) return;
    el.chatInput.value = "";
    askQuestion(q);
  });

  // ---------- settings: connections (Obsidian / Notion) ----------
  // These write config.toml through the daemon so the user connects a
  // destination from the UI instead of hand-editing TOML. The token is never
  // sent back to the page; a set token shows only as a connected state.

  function renderConnector(prefix, connected, label) {
    const card = document.getElementById(`connector-${prefix}`);
    const status = card.querySelector("[data-status]");
    status.textContent = connected ? label || "Connected" : "Not connected";
    status.classList.toggle("connected", connected);
    card.classList.toggle("is-connected", connected);
  }

  let notionTokenSet = false; // a token is on file (never sent back to the page)

  function applySettings(cfg) {
    el.obsidianVault.value = cfg.obsidian_vault || "";
    renderConnector("obsidian", !!cfg.obsidian_vault);
    el.obsidianDisconnect.hidden = !cfg.obsidian_vault;
    el.obsidianConnect.textContent = cfg.obsidian_vault ? "Save" : "Connect";

    el.notionDatabase.value = cfg.notion_database_id || "";
    // The token is a secret we never receive back; leave the field blank and
    // let the placeholder show a token is on file.
    el.notionToken.value = "";
    el.notionToken.placeholder = cfg.notion_token_set ? "•••••••• (saved — leave to keep)" : "ntn_…";
    notionTokenSet = !!cfg.notion_token_set;
    renderConnector("notion", cfg.notion_configured);
    el.notionDisconnect.hidden = !(cfg.notion_configured || cfg.notion_token_set);
    el.notionConnect.textContent = cfg.notion_configured ? "Save" : "Connect";
  }

  async function loadSettings() {
    try {
      const cfg = await (await apiGet("/api/settings")).json();
      applySettings(cfg);
    } catch (err) {
      toast("Could not load your connections.", "error");
    }
  }

  function openSettings() {
    el.settingsModal.hidden = false;
    loadSettings();
  }

  function closeSettings() {
    el.settingsModal.hidden = true;
  }

  el.settingsBtn.addEventListener("click", openSettings);
  el.settingsClose.addEventListener("click", closeSettings);
  el.settingsModal.addEventListener("click", (evt) => {
    if (evt.target === el.settingsModal) closeSettings(); // backdrop click
  });
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape" && !el.settingsModal.hidden) closeSettings();
  });

  async function saveConnector(method, path, body, okMsg) {
    let resp;
    try {
      resp = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      toast("Could not reach the daemon.", "error");
      return;
    }
    if (!resp.ok) {
      let detail = "Could not save your connection.";
      try {
        detail = (await resp.json()).detail || detail;
      } catch (err) {
        /* keep the generic message */
      }
      toast(detail, "error");
      return;
    }
    applySettings(await resp.json());
    toast(okMsg);
  }

  el.obsidianConnect.addEventListener("click", () => {
    const vault = el.obsidianVault.value.trim();
    if (!vault) {
      toast("Enter a vault folder path.", "error");
      return;
    }
    saveConnector("PUT", "/api/settings/obsidian", { vault }, "Obsidian connected.");
  });

  el.obsidianDisconnect.addEventListener("click", () => {
    saveConnector("DELETE", "/api/settings/obsidian", null, "Obsidian disconnected.");
  });

  el.notionConnect.addEventListener("click", () => {
    const token = el.notionToken.value.trim();
    const database_id = el.notionDatabase.value.trim();
    if (!database_id) {
      toast("Enter the Notion database ID.", "error");
      return;
    }
    if (!token && !notionTokenSet) {
      toast("Paste your Notion integration token.", "error");
      return;
    }
    // Omit a blank token so the daemon keeps the one already on file.
    const body = token ? { token, database_id } : { database_id };
    saveConnector("PUT", "/api/settings/notion", body, "Notion connected.");
  });

  el.notionDisconnect.addEventListener("click", () => {
    saveConnector("DELETE", "/api/settings/notion", null, "Notion disconnected.");
  });

  // ---------- controls ----------

  // Re-fetch the daemon's status and apply it. This is the self-heal path:
  // whenever the page's mirrored state disagrees with the daemon (missed
  // WebSocket event, sleeping laptop, failed request), one resync puts the
  // buttons back in a usable state instead of leaving the UI wedged.
  async function resyncStatus() {
    try {
      applyStatus(await fetchStatus());
    } catch (err) {
      /* daemon unreachable: the WS close handler shows the retry card */
    }
  }

  window.addEventListener("focus", resyncStatus);

  function markRecordPending() {
    recordPending = true;
    updateSessionBar();
    // Failsafe: if no status event arrives (dead socket), unlock and resync
    // rather than leaving the button disabled forever.
    setTimeout(() => {
      if (recordPending) {
        recordPending = false;
        resyncStatus();
      }
    }, 5000);
  }

  async function startRecording() {
    const title = el.titleInput.value.trim();
    // Clear + switch view *before* the request so the brief the daemon emits
    // during start (delivered over the socket right after) isn't wiped by a
    // late clear. Starting from idle, the transcript is already empty anyway.
    clearTranscript();
    state.currentNote = null;
    setView("transcript");
    let resp = null;
    try {
      resp = await apiPost("/api/record/start", {
        title: title || null,
        template: el.templateSelect.value || null,
      });
    } catch (err) {
      /* network error: handled below */
    }
    if (resp && resp.ok) return;
    recordPending = false;
    toast(
      resp && resp.status === 409
        ? "The daemon is busy with another session — try again in a moment."
        : "Could not start recording.",
      "error"
    );
    await resyncStatus();
    if (state.status.state === "idle") setView("empty");
  }

  async function stopRecording() {
    let resp = null;
    try {
      resp = await apiPost("/api/record/stop");
    } catch (err) {
      /* network error: handled below */
    }
    if (resp && resp.ok) return;
    recordPending = false;
    toast("Could not stop the recording.", "error");
    await resyncStatus();
  }

  function onRecordClick() {
    // "New meeting" also works while watching: the daemon preempts the idle
    // watch, records, and re-arms the watch when the note is saved.
    if (recordPending) return;
    markRecordPending();
    if (state.status.state === "recording" || state.status.state === "starting") {
      stopRecording();
    } else {
      startRecording();
    }
  }

  el.recordBtn.addEventListener("click", onRecordClick);
  el.emptyRecordBtn.addEventListener("click", onRecordClick);

  el.watchBtn.addEventListener("click", async () => {
    let resp = null;
    try {
      if (state.status.state === "watching" || state.status.state === "prompting") {
        resp = await apiPost("/api/watch/stop");
      } else {
        resp = await apiPost("/api/watch/start");
      }
    } catch (err) {
      /* network error: handled below */
    }
    if (resp && resp.ok) return;
    toast(
      resp && resp.status === 409
        ? "Already busy — can't do that right now."
        : "Could not reach the daemon.",
      "error"
    );
    await resyncStatus();
  });

  // Meeting prompt: answer the daemon's record/ignore question. The popup
  // itself hides when the next status event flips the state.
  async function respondToPrompt(accept) {
    el.meetingRecordBtn.disabled = el.meetingIgnoreBtn.disabled = true;
    let resp = null;
    try {
      resp = await apiPost("/api/watch/respond", { accept });
    } catch (err) {
      /* network error: handled below */
    }
    el.meetingRecordBtn.disabled = el.meetingIgnoreBtn.disabled = false;
    if (resp && resp.ok) return;
    toast(resp && resp.status === 409 ? "That meeting prompt has expired." : "Could not reach the daemon.", "error");
    await resyncStatus();
  }
  el.meetingRecordBtn.addEventListener("click", () => respondToPrompt(true));
  el.meetingIgnoreBtn.addEventListener("click", () => respondToPrompt(false));

  // ---------- WebSocket ----------

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/api/events`;
  }

  function connectEvents() {
    const ws = new WebSocket(wsUrl());

    ws.addEventListener("open", () => {
      state.wsRetryMs = 1000;
      clearTranscript();
      refreshNotes();
      fetchStatus()
        .then((status) => {
          applyStatus(status);
          syncScratchpad();
        })
        .catch(() => {});
    });

    ws.addEventListener("message", (msg) => {
      let evt;
      try {
        evt = JSON.parse(msg.data);
      } catch (err) {
        return;
      }
      handleEvent(evt);
    });

    ws.addEventListener("close", async () => {
      // A dropped socket with a live daemon just reconnects; a dead daemon
      // sends the page back to the boot loop so the retry card shows instead
      // of a stale session bar.
      try {
        await fetchStatus();
      } catch (err) {
        state.daemonUp = false;
        el.daemonDown.hidden = false;
        el.app.hidden = true;
        setTimeout(boot, 2000);
        return;
      }
      scheduleReconnect();
    });
    ws.addEventListener("error", () => ws.close());
  }

  function scheduleReconnect() {
    setTimeout(connectEvents, state.wsRetryMs);
    state.wsRetryMs = Math.min(state.wsRetryMs * 2, 15000);
  }

  function applyStatus(status) {
    const prev = state.status.state;
    state.status = status;
    recordPending = false; // the daemon answered; buttons follow real state again
    // A fresh session starts with an empty scratchpad; a mid-session reconnect
    // repopulates it from the daemon (syncScratchpad) instead of wiping it.
    const sessionStates = ["starting", "recording", "watching"];
    if (prev === "idle" && sessionStates.includes(status.state)) {
      el.scratchpad.value = "";
      scratchpadErrorShown = false;
    }
    updateSessionBar();
  }

  function handleEvent(evt) {
    switch (evt.type) {
      case "status":
        applyStatus({
          state: evt.state,
          title: evt.title,
          started: evt.started,
          elapsed_s: state.status.elapsed_s,
        });
        break;
      case "line":
        appendTranscriptLine(evt);
        break;
      case "echoes_dropped":
        appendTranscriptNotice(`${evt.count} echoed line${evt.count === 1 ? "" : "s"} dropped`);
        break;
      case "brief":
        showBrief(evt);
        break;
      case "summarizing":
        toast(`Summarizing with ${evt.model}…`);
        break;
      case "saved":
        toast(`Saved “${evt.title}”`);
        // Don't steal the view (or pop a confirm) out from under an edit.
        refreshNotes().then(() => {
          if (!state.editing) openNote(evt.name);
        });
        break;
      case "error":
        toast(evt.message, "error");
        break;
      default:
        break;
    }
  }

  // ---------- deep links (#note=<name>, used by the desktop tray) ----------

  function applyNoteHash() {
    const m = location.hash.match(/^#note=(.+)$/);
    if (m) openNote(decodeURIComponent(m[1]));
  }
  window.addEventListener("hashchange", applyNoteHash);

  // ---------- boot ----------

  async function boot() {
    try {
      const status = await fetchStatus();
      state.daemonUp = true;
      el.daemonDown.hidden = true;
      el.app.hidden = false;
      applyStatus(status);
      await refreshNotes();
      refreshArchived(); // populates the Archived tab count; non-blocking
      await syncScratchpad();
      await loadTemplates();
      applyNoteHash();
      startElapsedTicker();
      connectEvents();
    } catch (err) {
      state.daemonUp = false;
      el.daemonDown.hidden = false;
      el.app.hidden = true;
      setTimeout(boot, 2000);
    }
  }

  boot();
})();
