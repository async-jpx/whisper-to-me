# Features

What each user-facing feature does, how to use it, and how it works
underneath. For the module map and data flow, see
[architecture.md](architecture.md).

---

## Recording (`wtm record`)

Live-transcribes a meeting from the microphone **and** system audio, then
summarizes and saves a Markdown note. `Ctrl-C` stops the recording (the
remaining audio is transcribed before saving).

```sh
uv run wtm record --title "Team sync"
```

- **Two sources, one timeline.** The mic is `You`; system audio (everything
  other apps play — Zoom, Teams, Meet in a browser) is `Others`. Every
  transcribed sentence carries the absolute time it was *captured*, so the two
  streams interleave into one coherent conversation. Speaker labels appear
  only when both sources are active.
- **System audio capture** uses a ScreenCaptureKit helper compiled on first
  use (needs Xcode Command Line Tools). It hears app audio even with the
  speakers muted. macOS asks for the "Screen & System Audio Recording"
  permission once per terminal app (restart the app after granting). If the
  helper is unavailable, loopback devices (BlackHole, Zoom/Teams virtual
  devices) are used as a fallback; with neither, only the mic is heard.
- **Live journal.** Every line is written to the note file the moment it is
  transcribed — a crash or kill never loses the transcript.
- Useful flags: `--device N` (mic), `--system-device N|off`, `--model`
  (Whisper size), `--language`, `--ollama-model`, `--context "attendees,
  agenda hints"`, `--template NAME`, `--diarize`, `--no-summary`.

## Echo handling (automatic)

When remote voices play through your speakers, the mic hears them too and the
same words would appear twice — once cleanly as `Others`, once garbled as
`You`. Two layers prevent this:

1. **Acoustic echo cancellation** (`echo_cancel.py`): the system-audio stream
   is exactly what the speakers play, so it serves as a reference. An adaptive
   filter learns the speaker→mic path (locking the delay between the streams
   first) and subtracts the echo from the mic signal before it is even
   chunked. Adaptation freezes while you speak, so the filter never learns to
   cancel your own voice.
2. **Text-level echo filter** (`dedup.py`): a backstop for the canceller's
   convergence window. A `You` line that starts nearly simultaneously with an
   `Others` line and matches its text is dropped. The timing gate is strict on
   purpose: a genuine quick reply often reuses the other speaker's words, so
   only near-simultaneous onsets get the loose text match.

`--keep-echoes` disables the text filter (for A/B comparison); `--no-aec`
disables the acoustic canceller.

## Automatic meeting detection (`wtm watch`)

Notion-style: leave it running and it takes notes whenever a meeting starts.

```sh
uv run wtm watch
```

- **Detection**: CoreAudio reports when *any* app opens the default input
  device (covers Zoom, Teams, Meet, FaceTime…); Zoom additionally gets precise
  start/end detection via its in-meeting helper process.
- **Title**: an explicit `--title` wins; otherwise the current Calendar.app
  event or the Zoom window topic is used (both purely local, permission
  gated); otherwise the summarizer infers a title from the conversation and
  the note is renamed to match.
- **End of meeting**: the Zoom helper exiting, or a configurable silence
  timeout (`--silence-timeout`, default 120 s).
- Each detected meeting gets its own note; the loop then waits for the
  trigger to clear before watching again. macOS notifications announce
  detection and the saved note.

## Summarization (`wtm summarize`, and automatic after recording)

Notes are summarized by a **local** Ollama model (default `llama3.2:3b`) into
fixed sections: TL;DR, Decisions, Action Items (as `- [ ]` checkboxes), Risks
& Blockers, Open Questions, and Discussion grouped by topic.

Rather than asking a small model to summarize a long transcript in one shot,
the pipeline extracts structured JSON facts from overlapping transcript
windows, merges them deterministically in Python (fuzzy dedupe; action-item
owners/dates filled in across windows), and makes one synthesis call over the
merged facts — so summary depth doesn't degrade with meeting length, and the
model is never asked to be faithful to more text than fits its context.

```sh
uv run wtm summarize transcript.md [--user-notes notes.txt] [--template standup]
```

Re-summarizing an existing note keeps its transcript and replaces the summary.

## Meeting templates (`wtm templates`, `--template`)

Templates change the *sections* of the summary per meeting type. Built-ins:
`default`, `one-on-one`, `standup`, `interview`, `sales-call`, `brainstorm`.

- Auto-suggested from the meeting title (e.g. a title containing "standup"
  picks the standup template); an explicit `--template` always wins.
- Add your own: drop a markdown file in
  `~/.config/whisper-to-me/templates/` — frontmatter (`name`, `description`,
  `match` keywords) plus the section block as the body. A file with the same
  name as a built-in overrides it. Read fresh on every use; no restart needed.
- Every template must keep an `## Action Items` section with `- [ ]` tasks
  (the UI's checkbox toggling depends on it); a template without one is
  skipped with a warning.
- Only the sections are swappable — the faithfulness rules ("never invent
  names, dates, or commitments") apply to every template and cannot be
  overridden.

## Live scratchpad (web UI)

While a recording runs, type your own notes into the scratchpad panel. At
save time they reshape the summary: the note opens with a **"Your Notes,
Expanded"** section that repeats each of your points and expands it with the
matching facts from the transcript — and your notes bias the fact extraction
itself, so what mattered to you is what gets pulled out. A point the
transcript doesn't support is marked "*Not discussed in the transcript*",
never padded with invented detail.

The scratchpad persists to a sidecar file on every edit (crash-safe) and is
cleared per meeting, so a second meeting in `watch` mode never inherits the
first one's notes.

## Ask your notes (`wtm ask`, UI 💬 chat)

Local RAG over the whole notes corpus — ask a question, get a cited answer.

```sh
uv run wtm ask "what did we decide about the exporter launch?"
```

- Retrieval runs on the local FTS5 index with OR-matching (a natural-language
  question shouldn't require every word to appear in a note), ranked by bm25.
- The top notes' summaries, plus the transcript lines that mention the
  question's terms (with a line of context), become numbered sources.
- One Ollama call answers using only those sources, citing them as `[n]`;
  the response lists only the notes actually cited. No match → a plain
  "couldn't find anything" with no model call.
- The UI chat keeps conversation history (folded into the prompt) so
  follow-up questions work.

## "Last time…" briefs

When a meeting starts with a *real* title (from you, the calendar, or Zoom —
never the timestamp placeholder), the most recent related note is found via
title search and its TL;DR is surfaced — on the console, in the UI, and as a
macOS notification in watch mode. Purely local, best-effort: any failure is
silent and never disturbs the recording.

## Speaker diarization (beta, opt-in)

Splits `Others` into `Speaker A`, `Speaker B`, … in the saved note (the live
view keeps showing `Others`).

```sh
uv sync --extra diarize                # installs SpeechBrain + torch (heavy)
uv run wtm record --diarize
```

Each `Others` utterance is embedded with a local ECAPA-TDNN speaker model
(downloads once, then offline) and the embeddings are clustered by cosine
distance. Conservative by design: unless at least two clusters each own ≥10%
of the speech, everything stays plain `Others` — it degrades, never crashes,
and never labels the mic side.

## Notes, editing, and search

- Notes are plain Markdown in `~/MeetingNotes` (configurable via
  `notes_dir` in config.toml or `--notes-dir`), named
  `YYYY-MM-DD-HHMM-title-slug.md`, with Obsidian-friendly YAML frontmatter
  (title, date, attendees, tags).
- The web UI (`wtm ui`) lists, renders, and edits notes; action-item
  checkboxes toggle in place. The note currently being recorded is
  read-only until it is saved.
- Sidebar search is full-text (SQLite FTS5, search-as-you-type with
  prefix matching), synced automatically — external edits are picked up with
  no watcher or reindex step. The index is a hidden file inside the notes
  directory; delete it any time to rebuild.

## Follow-up email drafts (`wtm draft`, UI export menu)

```sh
uv run wtm draft ~/MeetingNotes/2026-07-06-team-sync.md
```

Drafts a follow-up email (subject, recap, decisions, action items with owners
and due dates) from the note's **summary** sections via the local model. The
draft goes to stdout / the clipboard — it is never sent anywhere, and the
transcript is deliberately excluded from the material.

## Obsidian export (`wtm export`)

```sh
uv run wtm export --obsidian ~/Vault/Meetings   # or set [obsidian] vault in config.toml
```

Copies notes into a vault as plain local files. Notes that predate
frontmatter get one retrofitted from their H1 title and filename date. Bulk
export skips notes already in the vault (never clobbers copies you may have
edited in Obsidian); the UI's per-note "Copy to vault" overwrites that one
note intentionally.

## Notion push (`wtm push`) — the one network feature

```sh
uv run wtm push 2026-07-06-team-sync.md
```

The **single sanctioned exception** to "nothing leaves the machine":

- Off unless you paste a `token` and `database_id` under `[notion]` in
  `~/.config/whisper-to-me/config.toml`.
- Pushes exactly **one note per explicit action** — CLI prompt or UI button —
  always behind a confirmation that previews what will be sent (title, date,
  attendees, block count, target database).
- Sends the note (converted to Notion blocks) to `api.notion.com` and nothing
  else — no telemetry, no other notes, and nothing ever calls it
  automatically.

The push adapts to your database's schema: it finds the title property, a
date property if one exists, and an `Attendees` multi-select if one exists.

## Web UI and daemon (`wtm serve` / `wtm ui`)

`wtm serve` runs a FastAPI daemon on `127.0.0.1:8737` (localhost-only,
hard-coded); `wtm ui` also opens the browser. The UI is fully vendored — no
CDN. It drives everything: record/watch/stop, live transcript over WebSocket,
scratchpad, template picker, notes browser/editor with search, chat, briefs,
and the export actions. Deep links (`#note=<name>`) open a specific note.

REST surface (all under `/api`): `status`, `record/start|stop`,
`watch/start|stop`, `simulate`, `session/scratchpad`, `notes` (list / get /
edit / toggle task), `search`, `chat`, `templates`, `export/config`,
`notes/{name}/vault|followup|notion`, and the `/api/events` WebSocket.

## Desktop app (menu-bar, `desktop/`)

A Tauri shell that puts the daemon in the macOS menu bar: a tray icon with
record/watch controls and elapsed time, a webview window on the local UI, and
native notifications. It spawns `wtm serve` as a sidecar — or, if a daemon is
already running on the port, attaches to it and never kills it. On quit the
spawned daemon gets SIGTERM (which saves and summarizes, exactly like Ctrl-C)
with a grace period; if it's still writing a note it is left running to
finish. See the README for build instructions.

## Testing without a microphone (`wtm simulate`)

```sh
uv run wtm simulate --mic you.wav --system others.wav [--diarize]
```

Replays audio files through the *entire* live pipeline — chunking,
transcription, echo cancellation, dedup, merging, summarization — on one
shared timeline, with no audio devices. This is the regression-test path for
the multi-source pipeline; `--keep-echoes` disables the echo filter for A/B
comparisons. Also exposed as `POST /api/simulate` for driving the daemon and
desktop shell in tests.
