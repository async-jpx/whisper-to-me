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
    view: "empty", // "empty" | "transcript" | "note"
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
    recordBtn: document.getElementById("record-btn"),
    watchBtn: document.getElementById("watch-btn"),
    notesList: document.getElementById("notes-list"),
    searchInput: document.getElementById("search-input"),
    emptyState: document.getElementById("empty-state"),
    transcript: document.getElementById("transcript"),
    noteContainer: document.getElementById("note-container"),
    noteView: document.getElementById("note-view"),
    noteEditor: document.getElementById("note-editor"),
    viewActions: document.getElementById("view-actions"),
    editActions: document.getElementById("edit-actions"),
    editBtn: document.getElementById("edit-btn"),
    copyBtn: document.getElementById("copy-btn"),
    saveBtn: document.getElementById("save-btn"),
    cancelBtn: document.getElementById("cancel-btn"),
    toasts: document.getElementById("toasts"),
  };

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

  // ---------- markdown rendering (vendored markdown-it, no CDN) ----------
  // html:false keeps raw HTML in notes escaped — content comes from speech
  // and a local LLM, so it is never trusted as markup. breaks:true gives the
  // single-newline transcript lines their own visual lines.

  const md = window
    .markdownit({ html: false, linkify: false, breaks: true })
    .use(window.markdownitTaskLists, { enabled: true });

  const STAMP_RE = /^\[(\d+:\d{2}:\d{2})\]$/;

  function renderNote(mdText) {
    el.noteView.innerHTML = md.render(mdText);
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

  // ---------- view switching ----------

  function setView(view) {
    state.view = view;
    el.emptyState.hidden = view !== "empty";
    el.transcript.hidden = view !== "transcript";
    el.noteContainer.hidden = view !== "note";
  }

  // ---------- session bar ----------

  function updateSessionBar() {
    const s = state.status;
    el.statusDot.className = "status-dot status-" + s.state;

    const labels = {
      idle: "Ready",
      recording: s.title || "Recording",
      watching: "Watching for meetings…",
      summarizing: "Summarizing…",
    };
    el.statusText.textContent = labels[s.state] || s.state;

    if (s.state === "recording" && s.started) {
      const startedMs = Date.parse(s.started);
      el.elapsed.textContent = Number.isNaN(startedMs)
        ? ""
        : formatElapsed((Date.now() - startedMs) / 1000);
    } else {
      el.elapsed.textContent = "";
    }

    el.recordBtn.disabled = s.state === "watching" || s.state === "summarizing";
    el.watchBtn.disabled = s.state === "recording" || s.state === "summarizing";
    el.titleInput.disabled = s.state !== "idle";

    if (s.state === "recording") {
      el.recordBtn.textContent = "Stop";
      el.recordBtn.classList.add("is-stop");
    } else {
      el.recordBtn.textContent = "Record";
      el.recordBtn.classList.remove("is-stop");
    }

    if (s.state === "watching") {
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

  function renderNotesList() {
    el.notesList.innerHTML = "";

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
      li.appendChild(btn);
      el.notesList.appendChild(li);
    }
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
    if (editorDirty() && !confirm("Discard your unsaved edits?")) return;
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
    el.noteEditor.hidden = false;
    el.viewActions.hidden = true;
    el.editActions.hidden = false;
    el.noteEditor.focus();
  }

  function exitEditMode() {
    state.editing = false;
    el.noteEditor.hidden = true;
    el.noteView.hidden = false;
    el.viewActions.hidden = false;
    el.editActions.hidden = true;
  }

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
  el.cancelBtn.addEventListener("click", () => {
    if (editorDirty() && !confirm("Discard your unsaved edits?")) return;
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

  function appendTranscriptNotice(message) {
    const row = document.createElement("div");
    row.className = "t-notice";
    row.textContent = message;
    el.transcript.appendChild(row);
    if (state.view === "transcript") maybeAutoScroll();
  }

  // ---------- controls ----------

  el.recordBtn.addEventListener("click", async () => {
    if (state.status.state === "recording") {
      await apiPost("/api/record/stop");
      return;
    }
    const title = el.titleInput.value.trim();
    const resp = await apiPost("/api/record/start", { title: title || null });
    if (resp.status === 409) {
      toast("Already busy — can't start a recording right now.", "error");
    } else if (!resp.ok) {
      toast("Could not start recording.", "error");
    } else {
      clearTranscript();
      state.currentNote = null;
      setView("transcript");
    }
  });

  el.watchBtn.addEventListener("click", async () => {
    if (state.status.state === "watching") {
      await apiPost("/api/watch/stop");
      return;
    }
    const resp = await apiPost("/api/watch/start");
    if (resp.status === 409) {
      toast("Already busy — can't start watching right now.", "error");
    } else if (!resp.ok) {
      toast("Could not start watching.", "error");
    }
  });

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
      fetchStatus().then(applyStatus).catch(() => {});
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
    state.status = status;
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
