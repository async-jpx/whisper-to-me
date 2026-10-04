# whisper-to-me

Local, private Notion-style meeting notes CLI (**macOS only**). Listens to the
mic and to system audio, live-transcribes with Whisper, summarizes with a local
Ollama model, saves markdown notes. Hard constraint: **nothing ever leaves the
machine** — no cloud APIs, no telemetry. Reject any change that violates this.
Sole sanctioned exception: the **Notion push** (`wtm push` / the UI's "Push to
Notion" button) sends exactly one note to `api.notion.com` — off unless the
user connects Notion (a token + database id, saved to config.toml by hand or
via the UI's Settings → Connections), per-note, user-initiated, behind a
confirmation showing what will be sent. Connecting only writes the token to
local disk — it must never verify credentials over the network; the token is
validated on first push. Nothing may ever call the push automatically; reject
any change that widens this path.

## Commands

```sh
uv sync                     # install deps (Python 3.12, managed by uv)
uv run wtm devices          # list audio inputs
uv run wtm record           # record + live-transcribe + summarize (Ctrl-C stops)
uv run wtm transcribe F     # audio file -> note
uv run wtm summarize F [--user-notes F] [--template NAME]  # re-summarize a transcript
uv run wtm ask "QUESTION"   # local RAG over all notes: cited answer from Ollama
uv run wtm draft NOTE.md    # follow-up email draft from a note (local, to stdout)
uv run wtm templates        # list meeting templates + where to add your own
uv run wtm simulate --mic F [--system F] [--diarize]  # replay files through the live pipeline
uv run wtm export [--obsidian PATH]  # copy notes into an Obsidian vault (local files)
uv run wtm push NOTE.md     # push ONE note to Notion (opt-in + confirmed; see above)
uv run wtm serve / wtm ui   # local daemon (127.0.0.1:8737) / + open web UI —
                            # meeting detection always on; a detected meeting
                            # is a 60 s Record/Dismiss prompt, never auto-recorded
uv run ruff check src/     # lint (also runs via hook on edits)
uv run pytest tests/       # unit + API tests
uv sync --extra diarize    # optional: speaker diarization stack (torch — heavy)
```

Web UI (React source in `webui/`, build output committed):

```sh
cd webui && npm install     # toolchain (build-time only; nothing loads from the network at runtime)
cd webui && npm run build   # tsc --noEmit + vite build -> src/whisper_to_me/static/dist (commit it)
cd webui && npm run dev     # Vite dev server on :5173, /api (incl. WS) proxied to a daemon on 8737
```

Phase-4 flags: `record`/`simulate` take `--diarize` (split "Others"
into Speaker A/B/C, beta); `record`/`simulate`/`transcribe`/`summarize`
take `--template NAME`; `summarize` also takes `--user-notes FILE`. The UI adds
a live scratchpad (notes-first summaries), a template picker, a 💬 Ask chat
view, "Draft follow-up email" in the Export menu, and "Last time…" briefs.

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
               outputs) → Python fuzzy merge → synthesis note + title inference.
               Synthesis prompt = SYNTH_HEADER + (notes instruction) + sections
               + SYNTH_RULES via _synth_system(); `user_notes`/`template` bias
               extraction + swap the sections block (rules stay last)
templates.py   meeting templates (default/one-on-one/standup/interview/
               sales-call/brainstorm) as templates/*.md + user overrides in
               ~/.config/whisper-to-me/templates/; suggest_template() matches by
               title; every template must keep a "## Action Items" + "- [ ]";
               the UI creates/deletes user templates (built-ins are read-only)
               and sets [templates] favorites/default in config.toml;
               resolve_template = explicit > configured default >
               suggest_template > None (built-in default), applied in
               runner.open_prompt and session.summarize_and_save
chat.py        local RAG (Phase 4.3): FTS5 retrieve (OR-match) → summary +
               term-matching transcript lines as numbered sources → one _chat
               call with [n] citations; sources filtered to those actually
               cited. prepare()/cited_sources() split retrieval from the model
               call so the daemon can stream tokens (summarize._chat_stream)
               in between
briefs.py      "Last time…" briefs: FTS find the most recent related note by
               title, extract its TL;DR; emitted as a "brief" event (never
               buffered in the replay), best-effort/never raises
followup.py    follow-up email draft from a note's summary (transcript dropped)
               via one _chat call; returned to the caller, never sent anywhere
diarize.py     speaker diarization within "Others" (beta, opt-in `--diarize` +
               `--extra diarize`): ECAPA embeddings (SpeechBrain, lazy) +
               numpy agglomerative cosine clustering; degrades to "Others" when
               the extra is missing or a cluster is unconfident
notes.py       markdown notes in ~/MeetingNotes; live journal + final rewrite;
               YAML frontmatter (title/date/attendees/tags) on saved notes
audio_store.py opt-in kept audio ([recording] keep_audio): each source's
               block_tap streams int16 into its own temp file placed by
               capture time (late/stalled tap = silent gap), mixed at stop
               → afconvert AAC `<note dir>/.wtm-audio/<stem>.m4a` +
               .peaks.json; follows the note (inferred-title move, archive,
               restore, delete); temp dirs carry the pid so only a dead
               process's are cleaned; unplayed 30 days (mtime) → purged
config.py      optional ~/.config/whisper-to-me/config.toml (notes_dir,
               [obsidian] vault, [notion] token+database_id, [templates]
               default+favorites); read fresh per
               use — no restart needed after edits; save_config writes it back
               for the UI's Settings → Connections (local disk, chmod 600, no
               network — connecting is never a token-verification call)
export.py      Obsidian vault copies: frontmatter retrofit for the
               back-catalog, skip-existing bulk export, per-note copy
notion_export.py  the sanctioned Notion push: markdown→blocks, page create;
               user-initiated only (wtm push / UI button), preview first
watch.py       meeting detection: CoreAudio mic-in-use + Zoom CptHost process;
               mic_in_use_by_others (macOS 14+ process objects) = the meeting-
               END signal while we hold the mic ourselves; title hints from
               Calendar.app / Zoom window (permission-gated). See
               docs/meeting-detection.md for the research + design
session.py     orchestration: sources ("You" mic / "Others" system), workers,
               per-segment timestamps, turn-merged transcript, summarize_and_save
runner.py      the daemon's meeting lifecycle as data: State = Idle(sitting_out)
               | Prompting(Prompt) | Active(SessionPlan, phase, started, stop),
               frozen values changed only by pure transitions (observe with
               the 60 s prompt deadline, open_prompt CAS, id-checked answer,
               start/stop); meeting_end_condition = the auto-stop for detected
               recordings (Zoom-helper exit / mic release / silence timeout)
server.py      FastAPI daemon (127.0.0.1 only): REST + /api/events WebSocket
               fan-out; single SessionManager owns the one runner.State and
               applies every change via _transition(fn) under _lock (status
               frames enqueued inside it; lock order _lock → _clients_lock);
               a detector thread (MeetingProbe seam) runs startup→shutdown,
               pauses while Active; prompts answered via POST
               /api/prompts/{id} {"answer": "record"|"dismiss"} (409 when
               stale/expired); record/start supersedes a prompt; record/stop
               stops manual + detected (not simulate), never detection; the
               shutdown hook (close) joins the session so SIGTERM saves it;
               /api/settings connects Obsidian/Notion by writing config.toml
               (save_config) — no network, the token never leaves via the wire;
               POST /api/chat/stream streams chat answers as SSE in the AI SDK
               UI-message-stream protocol (text-delta events + a data-sources
               part), still 127.0.0.1→local Ollama only
search.py      SQLite FTS5 index over notes (a cache: PRAGMA user_version =
               SCHEMA_VERSION, mismatch → drop + rebuild); notes_fts (one row
               per note) matches/ranks — search_notes has match_all (AND,
               sidebar default) vs OR (chat/briefs) mode, bm25 (title ×5) ×
               recency; hits_fts (title/summary/one row per transcript line
               with t seconds + speaker) feeds search() = GET /api/search,
               ≤3 hits per note so the UI can seek to the second
webui/         web UI source: React 18 + TypeScript strict + Tailwind v4 +
               Vite + Zustand, all deps bundled locally (no CDN, no runtime
               network). Displayed as "Hush". src/app.css holds the design
               tokens (:root light + prefers-color-scheme dark: --bg,
               --surface, --text*, --accent #2383e2/#4dabf7, radii, shadows)
               and component classes; Tailwind utilities (no preflight)
               layer on top via @theme tokens (bg-surface, text-accent…).
               store.View routes the main pane (home | live | note | chat |
               templates | settings); navigate() guards unsaved edits and
               closes the narrow-window drawer. <Composer> is the one text
               input (Enter sends, Shift+Enter newline). store.ts + ws.ts are the WS-authoritative
               status model (backoff reconnect, resync-on-focus, recordPending
               5s failsafe); api/types.ts `Status` is a union mirroring
               status_wire (idle | prompting+prompt | session phase+origin);
               only Active phases switch to the live view or show "Live
               session". The web UI never answers prompts — the status line
               just says "Meeting detected: …" and New meeting stays enabled
               (it supersedes the prompt); the overlay and tray answer.
               Components cover #note= deep links, live
               scratchpad, template picker, chat view, briefs, the Settings
               page (General: keep recordings; Connections), sidebar notes
               grouped by day or app (lib/noteGroups.ts, choice in
               localStorage), export menu (incl. the confirmed Notion push).
               Editors are CodeMirror 6
               (MarkdownEditor.tsx + lib/cm.ts commands); the chat view runs
               on @ai-sdk/react useChat (module-level Chat instance keeps the
               conversation across view switches) against /api/chat/stream
static/        dist/ — the committed Vite build, served at / (Cache-Control:
               no-cache; assets are content-hashed) and /static/dist/*; and
               prompt.html — a standalone hand-written widget page for the
               desktop overlay, hard-coded as /static/prompt.html in
               desktop prompt.rs — keep it a plain static file. It answers
               POST /api/prompts/{id}, counts down from expires_in_s (display
               only — never posts a dismiss at 0) and re-fetches /api/status
               on any non-2xx so its buttons follow the latest frame
cli.py         thin argparse wiring only — keep logic out of here
desktop/       Tauri menu-bar shell: spawns .venv/bin/wtm serve as a sidecar
               (or attaches to a running daemon and never kills it), webview →
               http://127.0.0.1:8737, tray mirrors /api/events (status frame
               parsed once into daemon::Status: Phase/Origin/Prompt; prompt
               answers POST /api/prompts/{id}), notifications (saved/error
               only — the overlay is the detection notice); prompt.rs shows
               the overlay (loads /static/prompt.html) once per new prompt
               id and hides it when `prompt` is null or the daemon goes
               offline — a non-activating NSPanel over full-screen Spaces;
               login_item.rs = tray "Launch at login" (bundled .app only):
               ~/Library/LaunchAgents/<identifier>.plist running
               `open -a <bundle>`, `Disabled` key = opted out
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
- The Notion push is the only code allowed to touch a non-localhost address,
  and only from `wtm push` / `POST /api/notes/{name}/notion` — both per-note
  and user-confirmed. Never wire it into record/detection/summarize/anything
  automatic, and never send the token anywhere but `api.notion.com`. Connecting
  Notion in the UI (`PUT /api/settings/notion` → `save_config`) only writes the
  token to local disk; it must stay a pure disk write — never add a "verify the
  token" network call there, or you've opened a second path off the machine.

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

- **Chat/brief retrieval must OR-match, not AND** (search.py `match_all`): the
  sidebar ANDs every query word (search-as-you-type), but a natural-language
  question ("when does the exporter ship and who owns…") ANDs to *nothing* —
  no note contains every word. chat.py and briefs.py pass `match_all=False`
  (OR) and lean on bm25 ranking + recency. Don't route a question through the
  AND path.
- **Diarization degrades silently by design** (diarize.py): `embed()` swallows
  any model/API error and returns None, and `assign_labels` returns `{}` unless
  ≥2 clusters clear MIN_CLUSTER_SHARE — so a wrong SpeechBrain import or an
  unconfident clustering just keeps plain "Others" instead of crashing. The
  flip side: a broken embedding path looks like "no speakers found", not an
  error. The import is `speechbrain.inference.classifiers.EncoderClassifier`
  (not `.speaker`) + `encode_batch`; confirm against the installed source.
- **Diarize relabel order is load-bearing** (session.py): relabel "Others" →
  Speaker A/B *after* `drop_echoes` + sort and *before* `_merge_turns`. Earlier
  and echo-dropped lines vote / the filter's "Others" comparisons break; later
  and turns don't follow real speakers. Only Others segments are embedded
  (never the mic), keyed by `(seg_at, text)`.
- **Default template must equal SYNTH_SECTIONS byte-for-byte**
  (templates/default.md): "no template" and `--template default` are asserted
  identical (test_templates). `_synth_system("")` must also reproduce the old
  single-string prompt exactly — the notes/template seams are additive only.
- **Checkbox DOM order ↔ task_index contract lives in NoteContainer's
  post-processors** (webui NoteContainer.tsx): the nth
  `input.task-list-item-checkbox` in DOM order PATCHes the nth task line —
  it only holds because markdown-it-task-lists' exact output matches the
  server's `_TASK_RE`. Don't "React-ify" the rendered note: the article is
  filled imperatively (innerHTML + hideNonSummary/linkStamps) and is
  deliberately NOT subscribed to `currentNoteMd` — a checkbox toggle
  refetches the markdown, and re-rendering there resets scroll. Re-renders
  happen only on note open (`currentNote`) and save (`noteRenderSeq`). The
  Summary tab *hides* the H1, the "Recorded …" line and the `## Transcript`
  section (the Transcript tab shows them from GET …/transcript); it must
  never remove them from the DOM, or a checkbox below them shifts index.
- **The webui editor keymap needs `Prec.high`** (lib/cm.ts): the editors are
  CodeMirror 6 (`<MarkdownEditor>`, note editor + scratchpad — CM's native
  history replaced the old execCommand hack). @uiw/react-codemirror registers
  basicSetup's keymaps *before* user extensions, so without `Prec.high` the
  default Enter binding wins and list/task continuation silently stops
  working. lib/cm.ts must keep the old textarea semantics: Enter continues
  "- [ ] " items, Enter on an empty item ends the list, Tab indents by two
  spaces.
- **Scratchpad sidecar stays out of the notes glob** (`.wtm-scratchpad.txt`,
  server.py): it's crash-safety for the live scratchpad, but it must never be
  a note — it works only because `*.md` globs and `_safe_note_path` exclude it.
  Cleared on session start and after each meeting's save (per meeting, or
  meeting 2 inherits meeting 1's notes).
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
- **Mic-release auto-stop must never count ourselves** (watch.py/runner.py):
  `mic_in_use_by_others` excludes our pid *and* every `Recorder.helper_pid`
  (the system-audio tap is a child process) — forget one and every detected
  recording runs forever (or worse, our own tap keeps "the meeting" alive).
  The signal also only arms after another process was actually seen on the
  mic (`call_app_seen`), and `None` (API missing, pre-macOS-14) must stay "no
  signal → silence timeout", never a stop (runner.meeting_end_condition).
- **Dismiss/timeout → `Idle(sitting_out=True)`, broadcast immediately**
  (runner.py/server.py): "sitting out" the rest of a handled meeting is a
  property of Idle, never a wire state — the frame says plain `idle` the
  instant a prompt is dismissed, times out, or a session ends, and the prompt
  surfaces hide on it. Don't model sit-out as a wait loop that delays the
  status (the old watch loop did, and the widget lingered for the whole
  meeting). A prompt answer must name the live prompt's id and beat its
  deadline (`runner.answer` → `Refused`); never reintroduce a pending-decision
  mailbox — with one, a late answer could be consumed by the next meeting's
  prompt and start recording without consent.
- **Echo filter must stay onset-aligned** (dedup.py): a genuine quick reply
  often reuses the other speaker's words ("Yes, it moved to Friday" right
  after "I think it was moved to Friday") and *will* fuzzy-match. Only a
  near-simultaneous start (~±1.5 s) may use the loose match; anything later
  needs a near-exact one. Loosening this deletes the user's own words.
- **Tray `set_title(None)` does not clear the title on macOS** (desktop
  tray.rs): after "summarizing" set the title to `Some("")`, or the "…" sticks
  in the menu bar forever. Verified the hard way; keep the always-`Some` form.
- **The meeting overlay must never activate us** (desktop prompt.rs): tao's
  `show()` is makeKeyAndOrderFront — it pulls the user out of a full-screen
  call Space. The overlay is built `focusable(false)` + `accept_first_mouse`,
  then (main thread only) re-classed in place to a runtime NSPanel subclass
  `WtmOverlayPanel` via `object_setClass`, given `NonactivatingPanel`,
  `hidesOnDeactivate = NO`, CanJoinAllSpaces|FullScreenAuxiliary|Stationary|
  IgnoresCycle, `NSStatusWindowLevel`, and shown with
  `orderFrontRegardless`. The swap is guarded: walk the class chain to
  `TaoWindow` (the live class is KVO's `NSKVONotifying_TaoWindow`) and
  require equal instance sizes + the same `focusable` ivar offset, else log
  and keep the plain window (still floats, but a click activates us). The
  private `_setPreventsActivation:` is called only if `respondsToSelector:`
  — without it a click on the panel still activates the app. The swap drops
  the KVO subclass, so the overlay is created once and never closed or
  destroyed (hide/order-out only); tearing it down is unexercised. Verify with
  CGWindowList (layer 25) + frontmost app over a full-screen Terminal.
- **An unsigned (ad-hoc) bundle loses its TCC grants on every rebuild**
  (desktop tauri.conf.json `bundle.macOS.signingIdentity`). The designated
  requirement of ad-hoc code is its cdhash, so TCC treats each `tauri build`
  as a new app. System Settings still shows the toggle on, but the
  system-audio helper exits with code 2 ("macOS denied…"). Tauri signs the
  bundle with "whisper-to-me local signing", a self-signed cert in the login
  keychain, so the requirement becomes the identifier plus the certificate
  leaf and survives rebuilds. To recreate the cert on a new machine, generate
  it with openssl (`extendedKeyUsage=codeSigning`) and run `security import
  … -T /usr/bin/codesign`. It can stay untrusted, and none of this needs
  admin. After any signing change, run `tccutil reset ScreenCapture
  io.github.asyncjpx.whispertome` and grant the permission again. Toggling
  the switch off and on does not refresh the stored requirement.
- **Keep `hardenedRuntime: false`** (desktop tauri.conf.json). Tauri turns
  hardened runtime on whenever a signing identity is set. A hardened app
  without the `com.apple.security.device.audio-input` entitlement gets silent
  mic buffers, with no prompt and no error, so every transcript comes out
  empty. Notarization is the only reason to harden, and a self-signed cert
  can't be notarized.
- **Notifications carry the app's identity only from a bundled build**: the
  bare `target/debug` binary's notifications are attributed to the terminal
  app (name + icon). Test identity with `npx tauri build --bundles app` and
  run the executable inside the produced `.app`.
- **Never SIGKILL the spawned daemon** (desktop daemon.rs): on quit it gets
  SIGTERM (= save + summarize, like Ctrl-C) and a 5 s grace; if still busy it
  is *left running* to finish the note. Also: a daemon that was already
  running on the port is not ours — attach, never kill.
- **launchd kills the daemon we leave running when a launchd-started app
  exits** (desktop daemon.rs): a LaunchAgent that execs the binary directly
  (even with AbandonProcessGroup or `process_group(0)`), *and* any
  LaunchServices launch — `open -a`, Finder, Dock, the login item; the app
  runs as job `application.<bundle-id>.*` — lose the daemon the moment the
  app exits. A SIGSTOPped daemon dies too, so it is a SIGKILL: a note still
  summarizing past the 5 s grace is lost. Only a shell-launched app's daemon
  survives. Not fixed yet; the fix is to run the daemon outside the app's
  job. Re-test with a SIGSTOPped daemon + tray Quit, comparing a shell
  launch against `open -a`.
- **Launch at login is bundle-only and plist-only** (desktop login_item.rs):
  offered only when `current_exe` sits in `<X>.app/Contents/MacOS/` (the dev
  binary shows it disabled). The plist runs `/usr/bin/open -a <bundle>` (a
  real app launch, the same as a double-click), never the binary itself.
  Every launch rewrites it so a moved .app never leaves a stale path; the
  opt-out is the `Disabled` key (launchd honours it), and the plist is the
  only state. It is never bootstrapped at runtime — it takes effect at the
  next login. If the user turns it off in System Settings → Login Items, the
  checkbox does not know.
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
   Anything leaving the machine is an automatic reject — except the one
   sanctioned, user-initiated Notion push path (see the header); even tests
   must not hit `api.notion.com` (mock `notion_export._request`).
5. Anything unverified (e.g. needs real mic/meeting audio, TCC permission,
   or a machine restart) is reported as unverified, not assumed working.

## Conventions

- Python 3.12, type hints, module docstrings; ruff clean.
- `cli.py` stays thin; new behavior goes in `session.py`/layer modules.
- New dependencies need a strong reason (local-only, small, maintained).
- GitHub via `gh` CLI (HTTPS remote; no SSH key on this machine).

@~/.claude/pstack-models.md
