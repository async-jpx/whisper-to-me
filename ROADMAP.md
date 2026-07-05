# whisper-to-me — Roadmap to a daily-driver app

Goal: move from proof-of-concept to an application someone (starting with us)
runs every working day. The privacy constraint stays absolute for everything
automatic: **nothing leaves the machine unless the user explicitly pushes it
out** (see "Export" below for the one deliberate, opt-in exception).

## Where we are today

Working: mic + system-audio capture (ScreenCaptureKit tap), AEC + text echo
dedup, live faster-whisper transcription, windowed Ollama summarization with
structured fact extraction, auto titles, crash-safe journaling, meeting
auto-detect (`watch`), a local FastAPI daemon (`wtm serve` / `wtm ui`) with a
WebSocket event stream, and a static web UI (sidebar, live transcript,
minimal markdown note view).

Missing for daily use: it lives in a terminal/browser tab, notes are
read-only and unsearchable, the markdown view is minimal, there's no way to
get notes into the user's PKM system, no diarization inside "Others", no test
suite, and no packaged distribution.

## What the competition teaches us

| Product | What they nail | What we copy | What we skip |
|---|---|---|---|
| [Granola](https://www.granola.ai/) | Bot-free capture; **your typed notes guide the AI summary**; templates per meeting type; chat across meetings; pre-meeting "briefs"; company grouping | Notes-first enhancement, templates, chat-with-notes, briefs — all feasible with local RAG + Ollama | Cloud ASR/LLM, accounts, pricing tiers |
| [Notion AI Meeting Notes](https://www.notion.com/) | Notes land where you already work; database properties (attendees, date, tags); linked references | Frontmatter metadata, attendee capture, backlink-friendly output | Cloud everything |
| [MacWhisper](https://goodsnooze.gumroad.com/l/macwhisper) | Local-first trust; full-text search over all transcripts; speaker diarization; watch folders; one-time purchase simplicity | Search index, diarization, batch/watch-folder transcription | Dictation/system-wide features (different product) |
| [Obsidian](https://obsidian.md/) | Local markdown vault as the source of truth; wikilinks; plugins | Vault-compatible output is our *native* format, not an export | — |
| Meetily / Mumble / Whisper Notes | Prove there's real demand for 100%-local meeting notes | Validation; watch their UX choices | — |

Our moat is the combination nobody else has: **Granola's UX with MacWhisper's
privacy** — live two-source capture, AEC, and local summarization in one tool.

---

## Phase 1 — Notes you can actually use (web UI, no new runtime)

The current UI shows notes; it must let you *work* with them. Everything here
ships in the existing FastAPI + static-frontend stack, so it's pure
incremental work and everything carries over into the Tauri shell later.

1. **Rich interactive markdown rendering** *(user-requested)*
   - Replace the minimal renderer with a vendored (no CDN — privacy)
     `markdown-it` or `marked`: tables, task lists, nested lists, code,
     footnotes.
   - Task-list checkboxes are **clickable** and write back to the note file
     via a new `PATCH /api/notes/{name}` (toggle `- [ ]` ↔ `- [x]`).
   - Transcript timestamps in the note become links that jump/scroll to that
     line in the transcript view.
   - Collapsible sections (transcript collapsed by default in long notes),
     copy-note / copy-section buttons.
2. **Note editing** — edit the summary/notes in place (textarea or CodeMirror
   vendored locally), save through the daemon. The journal-rewrite invariant
   in `notes.py` must be respected (never clobber a live session's file).
3. **Full-text search** — SQLite FTS5 index over titles, summaries, and
   transcripts (stdlib `sqlite3`, zero new deps). `GET /api/search?q=`.
   Sidebar search box. This is the single biggest daily-use win: "what did we
   decide about X in March?"
4. **Action-item tracker** — the summarizer already extracts structured action
   items; persist them per-note and add an aggregate "Open action items"
   view across all notes, with checkboxes that write back into the source note.
5. **Settings surface** — `GET/PUT /api/settings` (Whisper model, Ollama
   model, notes dir, AEC/echo toggles, language) persisted to
   `~/.config/whisper-to-me/config.toml`; a settings panel in the UI. Today
   these are CLI flags only, which blocks a double-click app.

## Phase 2 — Desktop app (Tauri) *(user-requested)*

Decision: **Tauri** (not Electron). Rationale: we already have a working
HTML/JS frontend and a localhost daemon; Tauri wraps them at ~10 MB instead of
~200 MB, has first-class menu-bar/tray support, and its Rust shell adds no
attack surface for a privacy product. The app is macOS-only, so a SwiftUI
wrapper was the alternative — rejected because it would force rewriting the
entire existing UI.

Architecture: Tauri shell → spawns the existing Python daemon as a
**sidecar** process (uv-managed venv or PyInstaller-frozen binary) → webview
loads `http://127.0.0.1:8737`. The daemon stays the single source of truth;
the CLI keeps working unchanged.

1. Tauri scaffold + sidecar lifecycle (spawn on launch, health-check, kill on
   quit; handle port-in-use = daemon already running).
2. **Menu-bar (tray) presence** — the daily-driver feature: recording status
   dot, Start/Stop, "Open last note", pause. `watch` mode makes this a true
   Granola-style "it just captures my meetings" experience.
3. Native notifications: "Meeting detected — recording", "Note saved: *Title*".
4. Login item (launch at startup, start in `watch` mode), dock-less mode.
5. Packaging: signed + notarized `.app`, DMG. TCC permissions (mic, system
   audio) get prompted for the *app bundle* — document the migration; this
   kills the "restart your terminal" wart, which is itself a UX win.
6. Later: replace the localhost round-trip with Tauri IPC only if profiling
   says it matters (it likely won't).

## Phase 3 — Export & interop *(user-requested)*

1. **Obsidian (primary, fully local)** — less an "export" than making our
   notes vault-native:
   - YAML frontmatter (`title`, `date`, `attendees`, `tags: [meeting]`,
     `source: whisper-to-me`).
   - Config option: write notes *directly into a vault folder* (notes dir =
     vault subfolder) — zero-step integration.
   - One-shot `wtm export --obsidian PATH` for the back-catalog, plus a
     per-note "Copy to vault" button.
2. **Notion (explicit opt-in — flagged constraint exception)** — Notion has
   no local API; export means a network call to `api.notion.com`. This
   violates the letter of "nothing ever leaves the machine", so it ships as:
   off by default, configured only by the user pasting an integration token,
   **per-note user-initiated push only** (button/CLI command — never
   automatic), with a confirmation showing exactly what will be sent. Markdown
   → Notion blocks conversion; target database with title/date/attendee
   properties. `CLAUDE.md`'s privacy rule gets amended to name this one
   sanctioned, user-initiated path.
3. **Clipboard/file exports** (free wins): copy as markdown, copy summary as
   Slack-friendly text, export PDF/HTML (local render).

## Phase 4 — Intelligence (catch up to Granola, locally)

1. **Speaker diarization within "Others"** — we know You vs Others; split
   Others into Speaker A/B/C via local embedding clustering
   (pyannote/SpeechBrain embeddings, macOS-friendly). MacWhisper's is "beta"
   quality — ours can be too; label it as such.
2. **Notes-first enhancement (Granola's core trick)** — a scratchpad in the
   live-session view; your typed bullets become anchors the summarizer must
   address and expand from the transcript. Our windowed fact-extraction
   pipeline already has the right shape for this (facts → merge → synthesis
   guided by user bullets).
3. **Chat with your meetings** — local RAG: FTS5 (+ optional local embeddings)
   retrieval over transcripts → Ollama answers with citations linking back to
   the note/line. Reuses the Phase-1 search index.
4. **Meeting templates** — per-type synthesis prompts (1-on-1, standup, sales
   call, interview, brainstorm), auto-suggested from calendar title, stored as
   editable markdown prompt files.
5. **Briefs** — before a detected meeting, surface the last note matching the
   same attendees/calendar event: "Last time you discussed…".
6. **Follow-up drafts** — "Draft follow-up email" button (local LLM, output to
   clipboard — never sent anywhere).

## Phase 5 — Engine & robustness (parallel track, ongoing)

1. **Test suite** — highest-leverage debt item. The `simulate` path was built
   for this: pytest + tiny fixture WAVs for chunker/dedup/AEC/merge; golden
   JSON tests for summarize windowing/merge (mock Ollama); API tests via
   FastAPI TestClient. CI on GitHub Actions (lint + unit; audio e2e stays
   local-only).
2. **Apple-Silicon-native ASR** — evaluate `mlx-whisper` (and Parakeet-MLX)
   vs current CPU int8 faster-whisper (~0.23× RT). GPU/ANE could cut that
   several-fold → headroom for bigger models or lower latency. Behind the
   existing model flag; benchmark with `/bench`.
3. **Resilience** — auto-recover the tap if the Swift helper dies mid-meeting;
   surface daemon-side errors as UI toasts + notifications; low-disk and
   Ollama-down handling ("transcript saved, summary pending — retry" instead
   of a lost summary; `wtm summarize` already covers the retry).
4. **Long-meeting audit** — 2-hour meeting: memory of the lines buffer,
   replay-buffer growth in the daemon, FTS index size, summarize-window count.

---

## Suggested order of attack

| Milestone | Contents | Why first |
|---|---|---|
| **M1: Usable notes** | Phase 1.1–1.3 (rich markdown, editing, search) + Phase 5.1 tests for what we touch | Daily value now; everything transfers into Tauri untouched |
| **M2: It's an app** | Phase 2.1–2.4 (Tauri, tray, notifications, login item) + Phase 1.5 settings | The "leave it running" moment — watch mode + tray = Granola workflow |
| **M3: It fits your system** | Phase 3 (Obsidian native, Notion opt-in, clipboard) + Phase 1.4 action items | Notes become part of the user's real workflow |
| **M4: It's smart** | Phase 4 in order: notes-first enhancement → templates → chat → diarization → briefs | Differentiators, each independently shippable |
| **M5: It's fast & solid** | Phase 5.2–5.4 + packaging polish (5.2 can start anytime) | Perf and robustness compound over time |

## Standing rules for all of the above

- Privacy: automatic paths never touch the network besides `127.0.0.1` and
  `localhost:11434` (Ollama). The Notion push is the sole exception: opt-in,
  per-note, user-initiated, previewed.
- No CDN assets, ever — vendor JS/CSS into `static/`.
- Every phase keeps the CLI fully functional; the daemon remains the single
  brain, UI and Tauri are thin clients.
- New deps need the strong-reason test (local-only, small, maintained);
  current candidates: `markdown-it` (vendored), Tauri toolchain, one
  diarization-embedding lib.
