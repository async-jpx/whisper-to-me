# whisper-to-me

Local, private Notion-style meeting notes CLI (**macOS only**). Listens to the
mic and to system audio, live-transcribes with Whisper, summarizes with a local
Ollama model, saves markdown notes. Hard constraint: **nothing ever leaves the
machine** — no cloud APIs, no telemetry. Reject any change that violates this.

## Commands

```sh
uv sync                     # install deps (Python 3.12, managed by uv)
uv run wtm devices          # list audio inputs
uv run wtm record           # record + live-transcribe + summarize (Ctrl-C stops)
uv run wtm watch            # auto-detect meetings, Notion-style
uv run wtm transcribe F     # audio file -> note
uv run wtm summarize F      # re-summarize a transcript
uv run wtm simulate --mic F [--system F]  # replay files through the live pipeline (no devices)
uv run wtm serve / wtm ui   # local daemon (127.0.0.1:8737) / + open web UI
uv run ruff check src/     # lint (also runs via hook on edits)
uv run pytest tests/       # unit + API tests
```

Desktop app (Tauri menu-bar shell, `desktop/`):

```sh
cd desktop && npm install                          # Tauri CLI (once)
python3 gen_icons.py && npx tauri icon app-icon.png  # regenerate icons
export PATH="/opt/homebrew/opt/rustup/bin:$PATH"   # cargo lives here (brew rustup)
cargo build --manifest-path src-tauri/Cargo.toml
WTM_PORT=8747 src-tauri/target/debug/whisper-to-me-desktop  # dev run on a scratch port
```

Audio/pipeline verification is behavioral (see Testing below); `tests/` covers
notes/search/API.

## Architecture

```
audio.py       Recorder (mic, 16kHz mono blocks -> energy-VAD utterance chunker,
               0.5s pre-roll, 0.2s min speech) + SystemAudioTap (spawns the Swift
               helper, pumps raw PCM into the same chunker) + build_system_tap()
system_audio_tap.swift  ScreenCaptureKit helper: all-app system audio -> 16kHz
               mono f32 on stdout; compiled on demand to ~/.cache/whisper-to-me/
transcribe.py  faster-whisper wrapper (large-v3-turbo int8, language lock-on)
dedup.py       cross-source echo filter: drops "You" segments that duplicate
               an "Others" segment (speaker bleed into the mic)
echo_cancel.py acoustic echo cancellation: system audio subtracted from the
               mic signal (envelope+fine delay lock, FDAF NLMS, Geigel
               double-talk freeze); text dedup stays on as backstop
summarize.py   Ollama pipeline: windowed JSON fact extraction (structured
               outputs) → Python fuzzy merge → synthesis note + title inference
notes.py       markdown notes in ~/MeetingNotes; live journal + final rewrite
watch.py       meeting detection: CoreAudio mic-in-use + Zoom CptHost process;
               title hints from Calendar.app / Zoom window (permission-gated)
session.py     orchestration: sources ("You" mic / "Others" system), workers,
               per-segment timestamps, turn-merged transcript, summarize_and_save
runner.py      watch_loop: meeting-detection loop shared by CLI and daemon
server.py      FastAPI daemon (127.0.0.1 only): REST + /api/events WebSocket
               fan-out; single SessionManager owns the one active session
search.py      SQLite FTS5 index over notes for GET /api/search
static/        vendored web UI (no CDN); app.js supports #note=<name> deep links
cli.py         thin argparse wiring only — keep logic out of here
desktop/       Tauri menu-bar shell: spawns .venv/bin/wtm serve as a sidecar
               (or attaches to a running daemon and never kills it), webview →
               http://127.0.0.1:8737, tray mirrors /api/events, notifications
```

Key invariants:
- Chunk queues carry `(capture_datetime, float32 @ 16 kHz mono)`; transcript
  lines are sorted by capture time, not transcription-completion time.
- Speaker labels (`**You:**` / `**Others:**`) only when >1 source is active.
- `notes.note_path(title, started)` is deterministic — the live journal and the
  final `save_note` rewrite target the same file. A crash mid-meeting must
  never lose transcript lines. With an auto-inferred title the final note gets
  a new path; `summarize_and_save` deletes the placeholder journal only *after*
  the final note is written, so one complete copy always exists.

## Testing (important etiquette)

- **Never open the user's microphone without asking** — they may be mid-meeting.
  Prefer tests that don't need the mic at all.
- No-mic end-to-end: `say -o /tmp/t.aiff "..."` → `afconvert -f WAVE -d LEI16@16000 -c 1`
  → `uv run wtm transcribe /tmp/t.wav`.
- Multi-source / echo-filter tests: `wtm simulate --mic a.wav --system b.wav`
  replays two files through the full chunker→transcribe→dedup→merge pipeline
  on one timeline (FileRecorder, sample-based timestamps). Compose fixtures by
  mixing the "others" waveform into the mic track at ~0.35 gain + ~120 ms
  delay to fake speaker bleed. `--keep-echoes` disables the filter for A/B.
- System-tap test: the helper exits on stdin EOF, so **hold stdin open**:
  `sleep 9 | ~/.cache/whisper-to-me/system-audio-tap > out.raw` (raw f32 @16k).
  It captures app audio **even with output muted** — use muted `say` or a
  YouTube tab for silent tests. For real-voice accuracy checks the user prefers
  a YouTube speech video over synthetic `say` voices.
- Stop a recording programmatically with SIGTERM to the *python* process
  (`pkill -TERM -f "bin/wtm"`) — it saves + summarizes like Ctrl-C. Don't
  SIGINT the `uv run` wrapper; uv doesn't forward it reliably.
- If you temporarily change system volume for a test, restore it (and mute
  state) afterwards.
- Desktop shell, mic-free: run it with `WTM_PORT=8747` so it spawns its own
  daemon instead of grabbing (or later killing) one the user left running on
  8737. The tray is scriptable without screenshots via accessibility — the
  status item is `menu bar 2` of the process: `osascript -e 'tell application
  "System Events" to get name of every menu item of menu 1 of menu bar item 1
  of menu bar 2 of (first process whose name contains "whisper-to-me")'`
  (also `enabled of …`, `click menu item "Quit" of …`, and the elapsed-time
  text via `value of attribute "AXTitle" of menu bar item 1`). Drive sessions
  with `POST /api/simulate {"mic": ..., "no_summary": true}` and quit through
  the tray so the SIGTERM cleanup path runs.

## Sharp edges (each cost real debugging time)

- **SCStream must stay strongly referenced** after `startCapture` — if the
  setup Task's local is the only reference, capture silently stops after ~1
  buffer. `activeStream` global exists for this.
- **SCStreamConfiguration needs a realistic video config** (640×360 @ 10 fps)
  even for audio-only capture; degenerate sizes (2×2 @ 1 fps) make SCStream
  deliver nothing at all, audio included.
- **TCC**: System Audio Recording permission is granted per terminal app and
  requires restarting that app. `CGPreflightScreenCaptureAccess()` gate is in
  the helper. Sandboxed shells can block the permission prompt entirely.
- **Ollama**: always send `options.num_ctx` (16k). Without it Ollama uses the
  model's max context — devstral once ballooned to a 58 GB KV cache and swapped
  the whole machine. Prefer small models (default llama3.2:3b); meeting notes
  don't need a 24B coder.
- **Zoom/Teams virtual audio devices carry no meeting audio in normal calls** —
  they're only loopback fallbacks. The ScreenCaptureKit tap is the real path.
- faster-whisper `large-v3-turbo` int8 ≈ 0.23× real-time on this M-series CPU;
  keep `condition_on_previous_text=False` for chunked live use, and keep the
  language lock-on (auto-detect flaps between languages on accented speech).
- Energy gate `SILENCE_RMS = 0.004` is deliberately permissive — Whisper's own
  VAD rejects noise downstream. Don't "fix" it upward without a listening test.
- **Echo filter must stay onset-aligned** (dedup.py): a genuine quick reply
  often reuses the other speaker's words ("Yes, it moved to Friday" right
  after "I think it was moved to Friday") and *will* fuzzy-match. Only a
  near-simultaneous start (~±1.5 s) may use the loose match; anything later
  needs a near-exact one. Loosening this deletes the user's own words.
- **Tray `set_title(None)` does not clear the title on macOS** (desktop
  tray.rs): after "summarizing" set the title to `Some("")`, or the "…" sticks
  in the menu bar forever. Verified the hard way; keep the always-`Some` form.
- **Notifications carry the app's identity only from a bundled build**: the
  bare `target/debug` binary's notifications are attributed to the terminal
  app (name + icon). Test identity with `npx tauri build --bundles app` and
  run the executable inside the produced `.app`.
- **Never SIGKILL the spawned daemon** (desktop daemon.rs): on quit it gets
  SIGTERM (= save + summarize, like Ctrl-C) and a 5 s grace; if still busy it
  is *left running* to finish the note. Also: a daemon that was already
  running on the port is not ours — attach, never kill.
- **FDAF adaptation must use the true error** (echo_cancel.py): adapt on
  `block − y_hat`, never on the protected output — adapting on the substituted
  signal keeps adding a full step to already-wrong weights and the filter
  diverges permanently. Likewise normalize by `|X|² + 1% mean bin power`; a
  bare 1e-8 epsilon lets near-empty bins blow the filter up.

## Definition of done (check before claiming a change works)

1. `uv run ruff check src/` is clean.
2. The changed path was exercised behaviorally, mic-free (see Testing):
   transcription/pipeline → `wtm transcribe` on a `say`-generated wav;
   multi-source/dedup/echo → `wtm simulate --mic ... --system ...`;
   summarization → `wtm summarize` on an existing transcript.
3. Diff re-checked against **Key invariants** and every **Sharp edge**
   naming a module you touched — those sections exist because plausible
   "fixes" broke them before.
4. Privacy audit of the diff: no network calls, no cloud APIs, no telemetry.
   Anything leaving the machine is an automatic reject.
5. Anything unverified (e.g. needs real mic/meeting audio, TCC permission,
   or a machine restart) is reported as unverified, not assumed working.

## Conventions

- Python 3.12, type hints, module docstrings; ruff clean.
- `cli.py` stays thin; new behavior goes in `session.py`/layer modules.
- New dependencies need a strong reason (local-only, small, maintained).
- GitHub via `gh` CLI (HTTPS remote; no SSH key on this machine).
