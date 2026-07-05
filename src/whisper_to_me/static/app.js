"use strict";

/* whisper-to-me — local web UI. Same-origin only; no external requests. */

(function () {
  const state = {
    status: { state: "idle", title: null, started: null, elapsed_s: null },
    notes: [],
    currentNote: null, // note name currently shown in the note view
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
    emptyState: document.getElementById("empty-state"),
    transcript: document.getElementById("transcript"),
    noteView: document.getElementById("note-view"),
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

  // ---------- minimal markdown renderer ----------
  // Supports exactly what notes.py / summarize.py produce: # / ## / ###
  // headings, **bold**, "* "/"- " bullet lists, "---" horizontal rules,
  // and paragraphs. All text is HTML-escaped before any markup is applied.

  function renderInline(text) {
    const escaped = escapeHtml(text);
    // notes.py wraps the "*Recorded ... — whisper-to-me*" subtitle line in a
    // single-asterisk emphasis; ** is replaced first so a lone * is never
    // mistaken for half of a bold marker.
    return escaped
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>");
  }

  function renderMarkdown(md) {
    const lines = md.replace(/\r\n/g, "\n").split("\n");
    let html = "";
    let paragraph = [];

    function flushParagraph() {
      if (paragraph.length) {
        html += `<p>${renderInline(paragraph.join(" "))}</p>`;
        paragraph = [];
      }
    }

    let i = 0;
    while (i < lines.length) {
      const raw = lines[i];
      const trimmed = raw.trim();

      if (trimmed === "") {
        flushParagraph();
        i++;
        continue;
      }

      if (trimmed === "---" || trimmed === "***") {
        flushParagraph();
        html += "<hr>";
        i++;
        continue;
      }

      const heading = trimmed.match(/^(#{1,3})\s+(.*)$/);
      if (heading) {
        flushParagraph();
        const level = heading[1].length;
        html += `<h${level}>${renderInline(heading[2])}</h${level}>`;
        i++;
        continue;
      }

      const bulletMatch = trimmed.match(/^[*-]\s+(.*)$/);
      if (bulletMatch) {
        flushParagraph();
        const items = [];
        while (i < lines.length) {
          const m = lines[i].trim().match(/^[*-]\s+(.*)$/);
          if (!m) break;
          items.push(m[1]);
          i++;
        }
        html += "<ul>" + items.map((it) => `<li>${renderInline(it)}</li>`).join("") + "</ul>";
        continue;
      }

      paragraph.push(trimmed);
      i++;
    }
    flushParagraph();
    return html;
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
    el.noteView.hidden = view !== "note";
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

    if (state.notes.length === 0 && state.status.state === "idle") {
      const empty = document.createElement("div");
      empty.className = "notes-empty";
      empty.textContent = "No notes yet.";
      el.notesList.appendChild(empty);
      return;
    }

    for (const note of state.notes) {
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
      btn.addEventListener("click", () => openNote(note.name));
      li.appendChild(btn);
      el.notesList.appendChild(li);
    }
  }

  async function refreshNotes() {
    try {
      state.notes = await fetchNotes();
      renderNotesList();
    } catch (err) {
      // Notes list is non-critical; leave the previous list showing.
    }
  }

  async function openNote(name) {
    try {
      const md = await fetchNoteContent(name);
      state.currentNote = name;
      el.noteView.innerHTML = renderMarkdown(md);
      setView("note");
      renderNotesList();
    } catch (err) {
      toast("Could not load that note.", "error");
    }
  }

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

    ws.addEventListener("close", scheduleReconnect);
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
        refreshNotes().then(() => openNote(evt.name));
        break;
      case "error":
        toast(evt.message, "error");
        break;
      default:
        break;
    }
  }

  // ---------- boot ----------

  async function boot() {
    try {
      const status = await fetchStatus();
      state.daemonUp = true;
      el.daemonDown.hidden = true;
      el.app.hidden = false;
      applyStatus(status);
      await refreshNotes();
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
