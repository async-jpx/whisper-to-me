# Architecture

whisper-to-me is a local, private meeting-notes pipeline for macOS. Everything
— audio capture, transcription, summarization, search, chat — runs on the
machine. The one sanctioned exception is the user-initiated Notion push (see
[Privacy model](#privacy-model)).

## The pipeline at a glance

```
 ┌──────────────┐   ┌───────────────────┐
 │ Microphone   │   │ System audio      │      (ScreenCaptureKit helper:
 │ (sounddevice)│   │ (Swift tap, all   │       every app's output, even
 └──────┬───────┘   │  apps' output)    │       with speakers muted)
        │           └─────────┬─────────┘
        │  raw 16 kHz mono    │
        ▼  0.1 s blocks       ▼
 ┌─────────────────────────────────────┐
 │ Acoustic echo cancellation          │  echo_cancel.py — subtracts the
 │ (system audio = reference signal)   │  speaker bleed from the mic signal
 └──────┬───────────────────┬──────────┘
        ▼                   ▼
 ┌─────────────────────────────────────┐
 │ Energy-VAD utterance chunker        │  audio.py — accumulates speech,
 │ (per source)                        │  flushes on trailing silence
 └──────┬───────────────────┬──────────┘
        ▼ "You" chunks      ▼ "Others" chunks
 ┌─────────────────────────────────────┐
 │ faster-whisper transcription        │  transcribe.py — per-segment
 │ (one worker thread per source)      │  timestamps, language lock-on
 └──────┬───────────────────┬──────────┘
        ▼                   ▼
 ┌─────────────────────────────────────┐
 │ Text-level echo filter (backstop)   │  dedup.py — drops mic lines that
 │ + capture-time sort + turn merge    │  duplicate a system-audio line
 │ + optional speaker diarization      │  diarize.py — Others → Speaker A/B
 └──────────────┬──────────────────────┘  session.py — orchestrates it all
                ▼
 ┌─────────────────────────────────────┐
 │ Summarization (local Ollama)        │  summarize.py — windowed fact
 │ extract → merge → synthesize        │  extraction, Python merge, one
 └──────────────┬──────────────────────┘  synthesis call, title inference
                ▼
 ┌─────────────────────────────────────┐
 │ Markdown note in ~/MeetingNotes     │  notes.py — YAML frontmatter,
 │ (live journal + final rewrite)      │  summary, timestamped transcript
 └─────────────────────────────────────┘
```

Everything above the note is a **live** pipeline: transcript lines appear (on
the console or in the UI) seconds after the words are spoken, and every line
is journaled to disk immediately so a crash never loses the transcript.

## Layers

The code splits into four layers. Higher layers depend on lower ones, never
the reverse.

### 1. Capture — getting audio into the pipeline

| Module | Role |
|---|---|
| `audio.py` | `Recorder` (mic via sounddevice/PortAudio), `SystemAudioTap` (spawns the Swift helper), `FileRecorder` (replays files for `wtm simulate`), and the shared energy-VAD utterance chunker they all feed. |
| `system_audio_tap.swift` | Standalone ScreenCaptureKit helper: captures every app's pre-mixer output audio, downmixes and decimates to 16 kHz mono float32, streams raw frames on stdout. Compiled on demand (`swiftc`) into `~/.cache/whisper-to-me/`. |
| `echo_cancel.py` | Acoustic echo cancellation (pure numpy): the system-audio stream is the reference, an adaptive frequency-domain filter (FDAF/NLMS) learns the speaker→mic path and subtracts the echo from mic blocks *before* chunking. Includes a two-stage delay lock (envelope correlation, then sample-level cross-correlation) and a Geigel double-talk detector that freezes adaptation while the user speaks. |

**How the chunker works** (`Recorder._chunk_loop`): audio arrives as 0.1 s
blocks of 16 kHz mono float32. A block whose RMS clears `SILENCE_RMS`
(deliberately permissive — Whisper's own VAD rejects noise downstream) counts
as speech. The chunker keeps a 0.5 s pre-roll so the first word isn't clipped,
flushes an utterance after 0.8 s of trailing silence (or at 30 s max), and
discards blips shorter than 0.2 s. Each flushed chunk is a
`(capture_datetime, float32_array)` tuple on the recorder's queue — the
capture timestamp is the backbone of everything downstream.

`FileRecorder` replays audio files through the identical chunker, with
timestamps computed from sample position against a shared epoch, so
`wtm simulate` exercises the exact live code path with no audio devices.

### 2. Transcript — turning chunks into ordered, clean text

| Module | Role |
|---|---|
| `transcribe.py` | Thin faster-whisper wrapper (`large-v3-turbo`, int8, CPU). `transcribe_chunk` returns per-segment `(start_s, end_s, text)` offsets relative to the chunk, so multiple sources interleave at sentence granularity. Locks onto the first confidently-detected language so auto-detection doesn't flap on accented speech. |
| `dedup.py` | Text-level echo filter. When remote voices play through speakers, the mic hears them too, so the same words arrive twice — cleanly as "Others", garbled as "You". A "You" line that starts nearly simultaneously (±1.5 s) with an "Others" line and fuzzy-matches its text is dropped; a line that merely starts *inside* the other's interval must match near-exactly (a genuine quick reply often reuses the other speaker's words). Backstop for the acoustic canceller's convergence window. |
| `diarize.py` | Optional (beta, `--diarize` + `uv sync --extra diarize`): splits "Others" into Speaker A/B/C using local ECAPA-TDNN embeddings (SpeechBrain) and numpy agglomerative cosine clustering. Degrades silently: any failure, or fewer than two confident clusters, keeps plain "Others". |
| `session.py` | The orchestrator. Builds the sources, wires the echo canceller between them, runs one transcription worker thread per source, applies the live echo filter, journals every line to disk as it lands, then (at stop) runs the final echo pass, sorts by **capture time** (not transcription-completion time), applies diarization labels, and merges consecutive same-speaker segments into turns. |
| `notes.py` | Markdown storage. `start_live_note` creates the journal up front; `append_line` adds each transcript line as it arrives; `save_note` writes the final note (YAML frontmatter → summary → timestamped transcript). `note_path(title, started)` is deterministic so the journal and the final rewrite target the same file. Atomic writes throughout. |

**Two-source model**: the mic is always `You`; system audio is always
`Others`. Speaker labels appear only when more than one source is active — a
solo mic recording produces an unlabeled transcript.

### 3. Intelligence — everything that runs through Ollama

All model calls go through `summarize._chat`, which always sends
`options.num_ctx = 16384` (without a cap Ollama uses the model's maximum
context, which can balloon the KV cache to tens of GB). Default model:
`llama3.2:3b`.

| Module | Role |
|---|---|
| `summarize.py` | The summarization pipeline: (1) window the transcript on line boundaries with overlap, (2) per window, structured JSON fact extraction (decisions, action items, risks, questions, attendees — an easier task for a small model than freeform summarizing), (3) fuzzy-merge the facts in Python, (4) one synthesis call writes the final Markdown sections, (5) one small call infers a title. Depth doesn't degrade with meeting length because synthesis sees merged facts, not raw transcript. |
| `templates.py` | Meeting templates: per-type synthesis section blocks (`templates/*.md` built-ins, user overrides in `~/.config/whisper-to-me/templates/`), auto-suggested from the meeting title. Only the sections block is swappable — the prompt header and faithfulness rules are fixed. Every template must keep `## Action Items` with `- [ ]` tasks. |
| `chat.py` | Local RAG over the notes corpus (`wtm ask`, UI 💬 view): FTS5 retrieval (OR-matched), then each hit's summary plus term-matching transcript lines become numbered sources, and one Ollama call answers with `[n]` citations. Sources are filtered to those actually cited. |
| `briefs.py` | "Last time…" briefs: when a meeting starts with a known title, FTS finds the most recent related note and surfaces its TL;DR. Best-effort — never raises, never disturbs a recording. |
| `followup.py` | Follow-up email drafts from a saved note's summary sections (transcript dropped). The draft is returned to the caller — never sent anywhere. |

### 4. Service & interface — driving the pipeline

| Module | Role |
|---|---|
| `watch.py` | Meeting detection signals: CoreAudio's "default input device is running" property (any app opening the mic), Zoom's CptHost helper process (runs only during a call), plus permission-gated title hints from Calendar.app and the Zoom window topic. |
| `runner.py` | `watch_loop`: the poll → detect → record → summarize → wait-for-meeting-end cycle, shared verbatim by `wtm watch` (CLI) and the daemon. |
| `search.py` | SQLite FTS5 index over the notes (`.wtm-index.sqlite3`, hidden inside the notes dir). Synced lazily on every search by mtime comparison — no watcher needed. `match_all=True` ANDs terms (sidebar search-as-you-type); `match_all=False` ORs them (chat/brief retrieval — a natural-language question ANDs to nothing). |
| `server.py` | FastAPI daemon, hard-bound to `127.0.0.1` (never configurable). REST endpoints + a `/api/events` WebSocket that fans every pipeline event out to all connected clients through bounded per-client queues (a slow client drops events rather than blocking the recorder). A single `SessionManager` owns the one active record/watch/simulate session and lazily loads the Whisper model once. |
| `config.py` | Optional `~/.config/whisper-to-me/config.toml` (notes dir, Obsidian vault, Notion credentials). Read fresh on every use — no daemon restart after edits. Malformed config falls back to defaults, never crashes. |
| `export.py` | Obsidian export: plain local file copies into a vault, retrofitting YAML frontmatter onto pre-frontmatter notes. Never overwrites an existing vault copy in bulk mode. |
| `notion_export.py` | The sanctioned Notion push: markdown → Notion blocks, one page created per explicit user action, preview shown first. The only module allowed to touch a non-localhost address. |
| `cli.py` | Thin argparse wiring only — all behavior lives in the layers above. |
| `static/` | Vendored web UI (no CDN): live transcript, scratchpad, template picker, chat view, note editor, export menu. |
| `desktop/` | Tauri menu-bar shell (`daemon.rs`, `tray.rs`): spawns `.venv/bin/wtm serve` as a sidecar (or attaches to an already-running daemon and never kills it), points a webview at the local UI, mirrors `/api/events` in the tray. On quit the spawned daemon gets SIGTERM + a 5 s grace (SIGTERM saves and summarizes, like Ctrl-C); if still busy it's left running to finish the note. |

## How the pieces talk

**Events.** `record_session`, `summarize_and_save`, and `watch_loop` all emit
through a single *event sink* — a callable taking one JSON-able dict (`status`,
`line`, `echoes_dropped`, `brief`, `summarizing`, `saved`, `error`). The CLI's
default `ConsoleSink` renders them as the console output; the daemon passes its
own sink that mirrors state and broadcasts to WebSocket clients. This one seam
is why the CLI and the UI share every code path.

**Threads.** A live session runs: one capture stream + one chunker thread per
source, one transcription worker per source, and (under the daemon) one
session thread. Queues carry `(capture_datetime, chunk)` between them; the
final transcript is assembled after all workers join.

**Timestamps.** Capture time is authoritative everywhere. Whisper segment
offsets are added to the chunk's capture time, giving every sentence an
absolute time; sources are interleaved by sorting on it. This is what makes
two independent audio streams read as one conversation.

## Crash safety

- The **live journal** is created before recording starts and every transcript
  line is appended immediately — a crash or `kill` mid-meeting loses nothing.
- `summarize_and_save` writes the final note (possibly under a new,
  auto-inferred title) **before** deleting the placeholder journal, so one
  complete copy always exists on disk.
- The UI **scratchpad** persists to a sidecar file (`.wtm-scratchpad.txt`,
  outside the `*.md` glob so it can never be mistaken for a note) on every
  edit.
- All note writes are atomic (write temp file, rename).

## Privacy model

The hard constraint: **nothing leaves the machine.**

- Whisper and the ECAPA diarization model download once from Hugging Face into
  a local cache, then run fully offline. Ollama is `localhost:11434`.
- The daemon binds `127.0.0.1` — hard-coded, not configurable.
- The web UI is fully vendored; no CDN fetches.
- The **single sanctioned exception** is the Notion push
  (`wtm push` / the UI's "Push to Notion" button): it sends exactly one note to
  `api.notion.com`, only when the user has pasted a token into `config.toml`,
  only per-note, only user-initiated, and only after a confirmation showing
  exactly what will be sent. Nothing may ever call it automatically.

Any change that widens this — a new network call, telemetry, an automatic
export — is rejected by design.

## Further reading

- [features.md](features.md) — what each user-facing feature does and how it
  works underneath.
- [../CLAUDE.md](../CLAUDE.md) — invariants, sharp edges, and testing
  etiquette for contributors.
- [../ROADMAP.md](../ROADMAP.md) — the phase plan this codebase follows.
