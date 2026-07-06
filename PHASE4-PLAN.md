# Phase 4 implementation plan — Intelligence (for the implementing agent)

You are implementing Phase 4 of ROADMAP.md ("Intelligence — catch up to
Granola, locally") in this repo. Read `CLAUDE.md` in full before touching
anything — its Key invariants, Sharp edges, Testing etiquette, and Definition
of done sections are binding. This plan tells you *what* to build and *where*;
CLAUDE.md tells you how to work and how to prove it works.

## Ground rules (apply to every milestone)

- **Privacy is absolute.** Every feature below talks only to
  `localhost:11434` (Ollama) or the local filesystem. No new network calls,
  no CDN assets (vendor everything into `static/`), no telemetry. The one
  pre-existing exception (Notion push) is not touched by Phase 4. The only
  gray zone you will hit is the diarization model download in Milestone D —
  it follows the exact precedent of faster-whisper: a one-time model download
  from Hugging Face into a local cache, offline forever after. Anything else
  leaving the machine is an automatic reject.
- **All Ollama calls go through `summarize._chat` / `summarize._chat_json`**
  (src/whisper_to_me/summarize.py:124-162). Never call
  `requests.post(OLLAMA_URL...)` yourself — `_chat` carries the
  `options.num_ctx = 16_384` cap that once saved this machine from a 58 GB
  KV cache (see Sharp edges). New prompt budgets must fit that context:
  keep total prompt input under ~24,000 chars (the same reasoning behind
  `WINDOW_CHARS`).
- **`cli.py` stays thin** — argparse wiring only. New behavior lives in new
  or existing layer modules.
- **Never open the microphone.** Every verification step below is mic-free:
  `wtm transcribe` on a `say`-generated wav, `wtm simulate --mic a.wav
  --system b.wav`, `wtm summarize` on an existing transcript, or FastAPI
  TestClient. If you think you need the mic, stop and report instead.
- **Tests mock Ollama.** Monkeypatch `summarize._chat` and
  `summarize._chat_json` (existing tests show the pattern); never require a
  live Ollama in `tests/`. Behavioral checks against real Ollama happen once
  per milestone, manually, via the CLI.
- **Per milestone, before moving on:** `uv run ruff check src/` clean →
  `uv run pytest tests/` green → the mic-free behavioral check listed in
  that milestone → re-read your diff against CLAUDE.md's Key invariants and
  every Sharp edge naming a module you touched → privacy audit of the diff.
  Then update `CLAUDE.md` (Architecture table + Commands if a new command
  exists) and tick the item in `ROADMAP.md`. Commit each milestone
  separately.

## Order

Ship in this order (ROADMAP's M4 ordering — each is independently valuable
and shippable):

- **A.** Notes-first enhancement (scratchpad-guided summaries) — Phase 4.2
- **B.** Meeting templates — Phase 4.4
- **C.** Chat with your meetings (local RAG) — Phase 4.3
- **D.** Speaker diarization within "Others" (beta) — Phase 4.1
- **E.** Briefs — Phase 4.5
- **F.** Follow-up drafts — Phase 4.6

A and B both modify the synthesis prompt in `summarize.py`; do A first and
design the prompt assembly so B slots in (details below). C reuses the
Phase-1 FTS index (`search.py`). D is the riskiest (new heavyweight optional
dependency) — it is deliberately *not* first; do not let it block A–C.

---

## Milestone A — Notes-first enhancement (Granola's core trick)

**Goal:** a scratchpad in the live-session view; whatever the user types
during the meeting becomes anchors the summarizer must address and expand
using facts from the transcript.

### A1. `summarize.py` — accept user notes

Change the signature:

```python
def summarize_meeting(
    transcript: str,
    model: str = DEFAULT_MODEL,
    context: str = "",
    user_notes: str = "",
) -> tuple[str, str | None, dict]:
```

- **Extraction pass:** user notes bias what's worth extracting. Reuse the
  existing `context` prefix mechanism in `_extract` (summarize.py:261-266):
  when `user_notes` is non-empty, append to the per-window prefix a line
  `The note-taker's own notes (facts related to these matter most):` followed
  by the notes **truncated to 2,000 chars** (the prefix repeats per window;
  it must not eat the window budget).
- **Synthesis pass:** refactor `SYNTH_SYSTEM` into two module constants —
  `SYNTH_HEADER` (the "You are a meeting-notes writer…" opening),
  `SYNTH_SECTIONS` (the `## TL;DR` … `## Discussion` block), and
  `SYNTH_RULES` (the "A section with no facts…" faithfulness tail) — plus a
  builder `def _synth_system(user_notes: str, sections: str = SYNTH_SECTIONS)
  -> str`. (The `sections` parameter is the hook Milestone B uses.) When
  `user_notes` is non-empty the builder inserts, **before** the sections
  block:

  ```
  The note-taker typed their own notes during the meeting. Open the output
  with one extra section, before all others:

  ## Your Notes, Expanded
  Repeat each of the note-taker's points as a short bold line, then expand
  it with the relevant extracted facts as sub-bullets. A point the facts
  say nothing about gets the single sub-bullet "*Not discussed in the
  transcript.*" — never invent support for it.
  ```

  and the synthesis *user* message gains, before the facts JSON:
  `Notes typed by the note-taker during the meeting:\n\n{user_notes}\n\n`
  (full text here, not truncated — one call, fits the budget for any sane
  scratchpad; if `len(user_notes) > 8000`, truncate to 8,000 and keep going).
- Faithfulness rules (`SYNTH_RULES`) always come last and are never
  template-overridable.

### A2. `session.py` — plumb through

`summarize_and_save` (session.py:305) gains `user_notes: str = ""` and passes
it to `summ.summarize_meeting`. Nothing else in this module changes.

### A3. `server.py` — scratchpad state + endpoints

- `SessionManager` gains `self._scratchpad: str = ""`. Reset it to `""`
  inside the locked section of `start_record`, `start_watch`, and
  `start_simulate` (next to `self._lines = []`).
- Add methods:

  ```python
  def set_scratchpad(self, text: str) -> None:
      with self._lock:
          if self.state == "idle":
              raise BusyError(self.state)
          self._scratchpad = text
      # crash-safety: the scratchpad must survive a daemon crash the same
      # way the live journal does
      notes.write_note_text(self.opts.notes_dir / ".wtm-scratchpad.txt", text)

  def get_scratchpad(self) -> str: ...   # lock, return self._scratchpad
  ```

  (`notes.write_note_text` is the existing atomic tmp+replace writer;
  `.wtm-scratchpad.txt` is outside the `*.md` glob so it can never appear as
  a note, be indexed, or be reachable through `_safe_note_path` — verify all
  three by reading those code paths, don't assume.) Ensure the notes dir
  exists before the first write (`mkdir(parents=True, exist_ok=True)`).
- In each session `run()` closure, pass `user_notes=self._scratchpad` to
  `summarize_and_save`, and after it returns delete
  `.wtm-scratchpad.txt` if present (`missing_ok=True`). For the **watch**
  mode there is no single `run()` call into `summarize_and_save` — the loop
  lives in `runner.watch_loop`. Plumb it the same way as the other options:
  add `user_notes_provider: Callable[[], str] | None = None` to
  `WatchOptions` is over-engineering; instead give `watch_loop` a new
  parameter `scratchpad: Callable[[], str] | None = None` and call it right
  before each `summarize_and_save`; the daemon passes
  `lambda: manager.get_scratchpad()` and clears the scratchpad
  (`manager._scratchpad = ""` under lock + file delete) after each meeting
  inside the loop is saved — otherwise meeting 2 of a watch session inherits
  meeting 1's notes. The CLI passes nothing and behaves exactly as before.
- Endpoints:

  ```python
  class ScratchpadBody(BaseModel):
      content: str

  @app.put("/api/session/scratchpad")      # 409 via BusyError when idle
  @app.get("/api/session/scratchpad")      # {"content": ...} ("" when idle)
  ```

  Cap `content` at 100_000 chars (reject 413) — a runaway client must not
  grow daemon memory unbounded.

### A4. Web UI — the scratchpad pane

- `index.html`: inside `#transcript`'s parent, restructure to
  `<div id="live-pane" class="live-pane" hidden>` containing the existing
  `#transcript` and a new `<div class="scratchpad"><div
  class="scratchpad-label">Your notes shape the summary</div><textarea
  id="scratchpad" placeholder="Type your own notes here — each point will be
  expanded in the final summary…"></textarea></div>`.
- `app.js`: the `setView("transcript")` case shows `#live-pane` instead of
  `#transcript` alone (rename the view or keep the name — keep `"transcript"`
  and just toggle the wrapper; the scroll handler and `appendTranscriptLine`
  keep targeting `#transcript`). On every scratchpad `input`, debounce 750 ms
  then `PUT /api/session/scratchpad`; on a failed PUT show one error toast,
  not one per keystroke (track a `scratchpadErrorShown` flag, reset on
  success). Clear the textarea when status transitions into a fresh session
  (`idle → recording` or `idle → watching`), **not** on
  `recording → summarizing` (the user may still be topping up, and the GET
  below re-syncs anyway). On WS reconnect / page load while a session is
  active, `GET /api/session/scratchpad` and populate the textarea.
- `style.css`: `.live-pane { display: flex; gap: … }`, transcript flexes,
  scratchpad ~320 px column, stacked vertically under ~900 px width. Match
  the existing visual language (borders, radius, fonts) — read the existing
  rules first.

### A5. CLI

`wtm summarize` gains `--user-notes FILE` (read the file, pass through).
`record`/`watch` get nothing — the terminal already *is* a place to type, and
the scratchpad's home is the UI.

### A6. Tests + verification

- `tests/test_summarize_notes.py` (or extend an existing summarize test
  file if one exists — check first): monkeypatch `_chat`/`_chat_json` to
  capture prompts; assert (1) user notes appear in the synthesis user
  message, (2) the "Your Notes, Expanded" instruction appears in the system
  prompt only when notes are non-empty, (3) the extraction prefix contains
  the truncated notes, (4) empty `user_notes` reproduces today's prompts
  byte-for-byte (regression guard).
- `tests/test_api.py`: PUT scratchpad while idle → 409; GET returns "";
  fake an active state via `client.manager` (existing tests show how) →
  PUT then GET round-trips; >100k chars → 413; `.wtm-scratchpad.txt` is
  written and is not listed by `GET /api/notes`.
- Behavioral (needs Ollama running): `uv run wtm summarize
  <existing transcript note> --user-notes /tmp/mynotes.md` where
  `/tmp/mynotes.md` has two bullets, one covered by the transcript and one
  not — confirm the expanded section handles both correctly. Then a full
  daemon pass: `uv run wtm serve` on a scratch port, `POST /api/simulate`
  with a `say`-generated wav, PUT a scratchpad line mid-run, confirm the
  saved note opens with "## Your Notes, Expanded".

---

## Milestone B — Meeting templates

**Goal:** per-meeting-type synthesis prompts (1-on-1, standup, sales call,
interview, brainstorm), auto-suggested from the calendar/Zoom title, stored
as editable markdown files.

### B1. New module `templates.py` + packaged defaults

- Ship built-in templates as package data:
  `src/whisper_to_me/templates/{default,one-on-one,standup,interview,sales-call,brainstorm}.md`.
  Confirm `uv_build` includes non-`.py` package files (the `static/` dir
  already ships this way — check how, and mirror it).
- Template file format — YAML frontmatter + body, parsed with the same
  hand-rolled tolerance as `notes.split_frontmatter` (no new YAML dep):

  ```markdown
  ---
  name: standup
  description: "Daily standup — per-person updates and blockers"
  match: [standup, stand-up, daily, scrum]
  ---
  ## TL;DR
  2-3 sentences on the team's overall state.

  ## Updates by Person
  A short bold name label, then their progress bullets.

  ## Action Items
  Bullets in the form "- [ ] task — owner (due date)"; omit owner/due when
  not given.

  ## Blockers
  Bulleted list.

  ## Open Questions
  Bulleted list.
  ```

  The body replaces `SYNTH_SECTIONS` in the `_synth_system` builder from
  Milestone A. `default.md`'s body is **exactly** today's `SYNTH_SECTIONS`
  so "no template" and "default template" are provably identical.
- API of the module:

  ```python
  @dataclass(frozen=True)
  class Template:
      name: str
      description: str
      match: tuple[str, ...]
      sections: str
      builtin: bool

  USER_TEMPLATES_DIR = CONFIG_PATH.parent / "templates"   # from config.py

  def list_templates() -> list[Template]   # builtins + user files; a user
                                           # file with the same name wins
  def load_template(name: str) -> Template | None
  def suggest_template(title: str | None) -> str | None
      # lowercase substring match of each template's `match` terms against
      # the title; first hit wins; None for no title / no hit
  ```

- **Hard validation on load:** every template body must contain a line
  `## Action Items` and mention `- [ ]` — the UI checkbox toggle
  (`notes.toggle_task` / `_TASK_RE`) and the roadmap's action-item tracker
  depend on that shape. A template failing validation is skipped with a
  console warning, never a crash (same philosophy as `load_config`: a typo
  in a user file must never take recording down).
- Read fresh per use, like `config.py` — no caching, no restart needed.

### B2. Plumbing

- `summarize_meeting(..., template: str | None = None)`: resolve via
  `templates.load_template(template)` when given; unknown name → raise
  `ValueError` early (CLI) / 400 (API) rather than silently summarizing
  wrong. Pass `tpl.sections` into `_synth_system`.
- `session.summarize_and_save(..., template: str | None = None)` → pass
  through.
- `cli.py`: `--template NAME` added in `_add_common` (covers record / watch /
  simulate / transcribe) and on `summarize`; plus a tiny `wtm templates`
  subcommand that prints a rich Table of name / description / source
  (builtin or the user file path) and where to put overrides.
- `runner.py`: `WatchOptions` gains `template: str | None`; in the loop,
  when `opts.template is None`, call `templates.suggest_template(hint)`
  (the calendar/Zoom hint — not the timestamp fallback title) and use the
  suggestion for that meeting's `summarize_and_save`.
- `server.py`: `ServerOptions.template: str | None = None`;
  `RecordStartBody` gains `template: str | None = None` (per-session choice
  beats the server default); `GET /api/templates` returns
  `[{name, description, builtin}]`. `SimulateBody` also gains it (cheap, and
  it makes the mic-free behavioral test possible).
- Web UI: a `<select id="template-select">` next to the title input,
  populated from `GET /api/templates` at boot (first option "Auto"),
  disabled while not idle, sent as `template` in the record-start body
  (`null` for Auto).

### B3. Tests + verification

- `tests/test_templates.py`: builtin discovery; user override shadows a
  builtin (tmp dir monkeypatched as `USER_TEMPLATES_DIR`); validation
  rejects a body without Action Items; `suggest_template("Weekly 1:1 with
  Sam")` → `one-on-one`, `suggest_template(None)` → None; frontmatter
  parsing tolerates a missing/malformed block.
- Prompt test: with `_chat` captured, `template="standup"` puts the standup
  sections in the system prompt and keeps `SYNTH_RULES` at the end;
  `template=None` is byte-identical to pre-template prompts.
- API: `GET /api/templates` lists; record-start accepts the field.
- Behavioral: `uv run wtm summarize <transcript> --template standup`
  (Ollama running) and confirm the note follows the standup sections;
  `uv run wtm templates` renders.

---

## Milestone C — Chat with your meetings (local RAG)

**Goal:** ask questions across all notes; Ollama answers with citations that
link back to the source notes. Retrieval = the existing FTS5 index.

### C1. New module `chat.py`

```python
"""Chat over the notes corpus — FTS5 retrieval + local Ollama, with
numbered citations. Nothing leaves the machine."""

SOURCE_BUDGET = 24_000      # total chars of source material per question
PER_SOURCE_CAP = 6_000
MAX_SOURCES = 6
MAX_HISTORY = 6             # prior turns forwarded to the model

def answer_question(
    notes_dir: Path,
    question: str,
    model: str = summarize.DEFAULT_MODEL,
    history: list[dict] | None = None,   # [{"role": "user"|"assistant", "content": str}]
) -> dict:   # {"answer": str, "sources": [{"n": int, "name": str, "title": str}]}
```

- **Retrieve:** `search.search_notes(notes_dir, question, limit=MAX_SOURCES)`
  — it already ranks, syncs the index lazily, and is safe on weird input.
  No hits → return
  `{"answer": "I couldn't find anything about that in your notes.",
  "sources": []}` **without calling Ollama**.
- **Build source blocks:** for each hit `[n]` (1-based), read the note
  (`notes_dir / name`), `notes.split_frontmatter`, then take: everything
  before the `^## Transcript$` line (the summary — always relevant), plus
  each transcript line containing any question term (case-insensitive,
  terms = `question.split()` minus terms shorter than 3 chars) with one line
  of context either side. Clamp each block to `PER_SOURCE_CAP`, stop adding
  blocks when `SOURCE_BUDGET` is reached. Format:

  ```
  [1] "Sprint Planning" (2026-06-12)
  <content>

  [2] ...
  ```

  (date from the frontmatter `date:` when present, else file mtime.)
- **Ask:** one `summarize._chat` call. System prompt (module constant):
  answer **only** from the numbered sources; put a citation like `[2]`
  immediately after each claim; if the sources don't cover the question,
  say so plainly; be concise; markdown allowed. Messages = system, then up
  to `MAX_HISTORY` history entries verbatim (validate roles to the two
  allowed values, drop anything else), then the user message =
  sources + `\n\nQuestion: {question}`.
- **Post-process:** `sources` in the return value = only the notes whose
  `[n]` actually appears in the answer text (regex `\[(\d+)\]`), so the UI
  never lists an unused source.
- `OllamaError` propagates — callers map it (CLI prints red + exit 1, API
  → 503).

### C2. `server.py`

```python
class ChatBody(BaseModel):
    question: str
    history: list[dict] = []

@app.post("/api/chat")
def chat_endpoint(body: ChatBody):
    q = body.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty question")
    try:
        return chat.answer_question(opts.notes_dir, q, model=opts.ollama_model,
                                    history=body.history)
    except summ.OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
```

A sync `def` endpoint — FastAPI runs it in the threadpool, so a slow Ollama
answer doesn't block the event loop or the WebSocket fan-out. Chat is
deliberately **independent of the session state machine** — asking questions
while idle, recording, or summarizing are all fine (Ollama itself serializes
requests; a chat during summarization just queues behind it — acceptable,
note it in the endpoint docstring).

### C3. Web UI

- New view `"chat"` alongside `"empty" | "transcript" | "note"`: add
  `<div id="chat-view" hidden>` to `#content` — a scrolling message list and
  a bottom form (`<textarea>` one-line autosize is overkill; a simple
  `<input type="text">` + Ask button; Enter submits).
- Entry point: a "💬 Ask" button in the sidebar header next to the brand
  (matching `.btn .btn-ghost .btn-sm` styling). Clicking it calls
  `setView("chat")` and clears `state.currentNote` highlight.
- Render: user messages as plain-text bubbles; assistant messages through
  the existing `md.render` (it's configured `html:false` — safe). After
  rendering, replace `[n]` citation markers in the assistant HTML with
  links: walk text nodes (same technique as `anchorStamps`,
  app.js:183-232), match `\[(\d+)\]`, and for numbers present in the
  response's `sources` array create an `<a class="cite" title="{title}">`
  that calls `openNote(name)` on click. Below each assistant message, a
  small source list ("1. Sprint Planning — 2. …") with the same click
  behavior.
- Keep the conversation in `state.chatHistory` (array of
  `{role, content}`), send the last `MAX_HISTORY` with each request, show a
  "Thinking…" placeholder bubble while the fetch is in flight, disable the
  form during it, error → toast + remove placeholder. History is in-memory
  only; a reload starts fresh (fine for v1 — note it).
- No streaming in v1. If answers feel too slow in practice, streaming via
  Ollama's `stream:true` over the existing WS is a follow-up, not now.

### C4. CLI

`wtm ask "QUESTION" [--notes-dir] [--ollama-model]` → prints the answer,
then a dim source list. Thin: parse args, call `chat.answer_question`,
print.

### C5. Tests + verification

- `tests/test_chat.py`: source-block builder (summary always included,
  matching transcript lines with context, per-source and total caps
  enforced, short question terms ignored); no-hits path returns without
  calling `_chat` (monkeypatch `_chat` to raise if called); citation
  filtering (answer citing `[1]` and `[3]` → sources 1 and 3 only); history
  role validation.
- API: empty question → 400; monkeypatch `chat.answer_question` for the
  happy path; `OllamaError` → 503.
- Behavioral: with Ollama running and a few real notes in a scratch dir,
  `uv run wtm ask "what did we decide about X"` → sensible cited answer;
  then in the UI, ask the same and click a citation → the note opens.

---

## Milestone D — Speaker diarization within "Others" (beta)

**Goal:** split the single "Others" source into "Speaker A/B/C" via local
speaker-embedding clustering. Post-hoc (labels appear in the saved note, the
live view keeps showing "Others"), opt-in (`--diarize`), labeled beta.
MacWhisper's is beta quality; ours is allowed to be too.

### D1. Dependency — optional extra

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
diarize = ["speechbrain>=1.0", "torch>=2.2", "torchaudio>=2.2"]
```

SpeechBrain's `spkrec-ecapa-voxceleb` ECAPA-TDNN embeddings: maintained,
CPU-fine on Apple Silicon, downloads once from Hugging Face into a local
cache (`~/.cache/…`) — the same offline-after-first-download precedent as
faster-whisper, so it passes the privacy bar; say so in the module
docstring. torch is heavy, which is exactly why this is an **extra**, never
a base dependency. Verify the actual embedding API against the installed
speechbrain source before writing code (`EncoderClassifier.from_hparams` +
`encode_batch` is the expected shape — confirm signatures, don't trust
memory).

### D2. New module `diarize.py`

```python
"""Speaker diarization within the "Others" source (beta) — local ECAPA
embeddings + agglomerative cosine clustering. Optional: requires
`uv sync --extra diarize`; degrades to plain "Others" when missing."""

MIN_SEGMENT_S = 1.0        # too short to embed reliably
COSINE_THRESHOLD = 0.68    # merge clusters closer than this (tune later)
MAX_SPEAKERS = 4
MIN_CLUSTER_SHARE = 0.10   # a "speaker" owning <10% of speech time is noise

def available() -> bool                  # import-probe, cached
class SpeakerEmbedder:
    def __init__(self) -> None           # lazy model load on first embed
    def embed(self, audio: np.ndarray) -> np.ndarray | None
        # f32 mono 16 kHz in; None for segments < MIN_SEGMENT_S or on any
        # model error (a failed embedding must never kill a transcription
        # worker — catch, warn once, return None)

def cluster(embeddings: list[np.ndarray]) -> list[int]
    # average-linkage agglomerative clustering on cosine distance,
    # implemented in plain numpy (corpus is a few hundred vectors — O(n³)
    # is irrelevant); stop merging at COSINE_THRESHOLD or MAX_SPEAKERS

def assign_labels(
    lines: list[dedup.Line],
    embeddings: dict[tuple[datetime, str], np.ndarray],
) -> dict[tuple[datetime, str], str]
    # keys are (captured_at, text) of "Others" lines; returns
    # {} (meaning: keep "Others") unless ≥2 clusters each hold
    # ≥ MIN_CLUSTER_SHARE of total embedded duration; labels are
    # "Speaker A", "Speaker B", … ordered by first appearance
```

### D3. `session.py` integration — placement is the whole game

- `record_session` gains `diarize: bool = False`. When true and
  `diarize.available()` and there is an "Others" source, create one
  `SpeakerEmbedder` and an `others_embeddings: dict[tuple[datetime, str],
  np.ndarray]` plus a `threading.Lock` for it.
- Inside `make_worker`, only for the "Others" worker, after a segment
  passes into `raw_lines`: slice the chunk to the segment
  (`chunk[int(start_s*16000):int(end_s*16000)]`), `embedder.embed(...)`,
  and on non-None store under key `(seg_at, text)`. This runs on the worker
  thread — embedding a segment takes tens of ms on CPU, and the "Others"
  worker only competes with Whisper decode, which dwarfs it. Do **not**
  embed mic ("You") segments.
- **Relabeling happens after `drop_echoes` and after the sort, immediately
  before `_merge_turns`** (session.py:230-241). Order is load-bearing three
  ways: (1) `dedup.drop_echoes`/`matches_any` compare against
  `CLEAN_SPEAKER = "Others"` — relabeling first would break echo removal
  silently; (2) echo-dropped lines must not vote in clustering — build the
  embeddings input from the *kept* lines only (look up each kept Others
  line's key in the dict; missing keys are simply un-embedded lines that
  keep the majority label — give them the label of the nearest-in-time
  labeled line, or "Others" if none); (3) `_merge_turns` coalesces on
  speaker equality, so relabeling before it makes turn boundaries follow
  real speakers — that is the feature.
- The label produced flows into the existing
  `f"**{speaker}:** {text}"` formatting untouched. Live `line` events keep
  saying "Others" (post-hoc design); the saved note shows Speaker A/B/C.
- Wire the flag: `--diarize` on `record`/`watch`/`simulate` in `cli.py`
  (help text: "split 'Others' into Speaker A/B/C — beta, needs `uv sync
  --extra diarize`"), `WatchOptions.diarize`, `ServerOptions.diarize`,
  passed to every `record_session`/`simulate_session` call site (grep for
  them all; there are call sites in cli.py, runner.py, server.py). When the
  flag is set but the extra isn't installed, print/emit one clear warning
  and proceed undiarized — never crash a recording over a missing nicety.
- `simulate_session` passes `diarize` straight through — this is the test
  path.

### D4. Summarizer awareness

`EXTRACT_SYSTEM` (summarize.py:66) currently explains `**You:**` /
`**Others:**`. Extend the sentence to also allow `**Speaker A:** /
**Speaker B:**` labels for distinct other participants. One line; do not
touch anything else in the prompt.

### D5. UI

`appendTranscriptLine` (app.js:668) already handles arbitrary speakers via
the `chip-you`/`chip-others` split — live view is unaffected (still
"Others"). The rendered *note* view just shows bold `**Speaker A:**` text —
no change required. Skip per-speaker chip colors for now.

### D6. Tests + verification

- `tests/test_diarize.py`, guarded with
  `pytest.importorskip` **only** for tests touching the real model; the
  clustering/labeling logic must be testable without torch: `cluster()` on
  synthetic unit vectors (two tight groups → 2 clusters; one group → 1;
  threshold respected); `assign_labels` guards (single cluster → `{}`;
  tiny cluster below MIN_CLUSTER_SHARE → absorbed/ignored; label order =
  first appearance); un-embedded-line fallback.
- Behavioral (the real check, mic-free): build a two-voice fixture —
  `say -v Daniel -o /tmp/a.aiff "..."` and `say -v Samantha -o /tmp/b.aiff
  "..."`, `afconvert` both to 16 kHz mono wav, concatenate with a gap into
  one "system" file (numpy or `afconvert`+`cat` of raw PCM — you have the
  chunker's format documented in CLAUDE.md), then
  `uv run wtm simulate --mic silence.wav --system twovoices.wav --diarize
  --no-summary` and confirm the note transcript shows Speaker A and
  Speaker B for the right halves. Also run once **without** the extra
  installed (or with the import forced to fail) to prove the graceful
  fallback. Then run the standard echo-filter simulate fixture *with*
  `--diarize` to prove dedup still drops the bleed (invariant 1 above).
- Report honestly: clustering thresholds tuned on synthetic `say` voices
  may need retuning on real meeting audio — the user prefers real-voice
  tests (YouTube speech video); flag this as the known follow-up.

---

## Milestone E — Briefs

**Goal:** when a meeting is detected (watch mode) or started with a title,
surface the most recent related note: "Last time you discussed…".

### E1. New module `briefs.py`

```python
def find_brief(notes_dir: Path, title: str, exclude: str | None = None) -> dict | None
    # {"name", "title", "modified", "tldr"} of the best prior note, or None
def _tldr(md: str) -> str
    # text between "## TL;DR" and the next "## " heading, whitespace-
    # collapsed, clamped to ~400 chars; fallback: first non-heading
    # paragraph after the H1; "" if neither exists
```

Retrieval: `search.search_notes(notes_dir, title, limit=5)` (it already
tokenizes/quotes safely), drop the `exclude` name (the note being recorded
right now — the live journal *will* match its own title), pick the entry
with the newest `modified`. Read the file, extract `_tldr`. Any `OSError`
→ None; this feature must never break a recording.

### E2. Emit it

- `runner.watch_loop`: right after `title` is chosen and **before**
  `record_session`, when the title came from a real hint (`hint is not
  None`) or from `opts.title`: `brief = briefs.find_brief(opts.notes_dir,
  title, exclude=notes.note_path(title, <now>, opts.notes_dir).name)` —
  actually the journal doesn't exist yet at this point, so `exclude=None`
  is fine; verify by reading the flow: `start_live_note` is called inside
  `record_session`, after this. If found:
  `sink({"type": "brief", "name": ..., "title": ..., "modified": ...,
  "tldr": ...})` and a `watch.notify("whisper-to-me", f"Last time:
  {brief['title']}")`. When escaping the notify message, note
  `watch.notify` interpolates into AppleScript — strip `"` and `\` from the
  message before passing (the existing function has this wart; don't make
  it worse).
- `server.py start_record`: after `_broadcast_status()` in the non-locked
  section, when the user supplied a title, run `find_brief` and `_sink` the
  same event (cheap: one FTS query + one file read). Skip for the
  auto-generated `Meeting {timestamp}` placeholder — it can't match
  meaningfully.
- `session.ConsoleSink.__call__`: add an `elif etype == "brief":` branch
  printing e.g. `[dim]Last time — {title}: {tldr}[/dim]`. (Unknown event
  types are silently ignored by ConsoleSink today; without this branch the
  CLI would never show briefs.)
- `SessionManager._sink` forwards non-status events as-is already — the
  brief reaches WS clients with no server change beyond emitting it. It is
  *not* added to the `_lines` replay buffer, so late-joining clients miss
  it; acceptable, note it in a comment.

### E3. Web UI

`handleEvent` gains `case "brief":` → render a dismissible card pinned at
the top of the transcript view (`#transcript`, before the lines): "📋 Last
time — **{title}**" + the tldr + an "Open note" link calling
`openNote(name)` (build with `createElement`/`textContent`, never
`innerHTML` with note-derived strings). `clearTranscript()` removes it
naturally.

### E4. Tests + verification

- `tests/test_briefs.py`: `_tldr` extraction (TL;DR present / absent /
  empty note); recency pick among multiple matches; no match → None;
  exclude honored.
- API-level: monkeypatch `briefs.find_brief` and assert `start_record` with
  a title broadcasts the brief event (drive via `client.manager._sink`
  capture or a WS test client if one exists in test_api.py — follow the
  existing pattern).
- Behavioral: create two notes titled around "Roadmap sync" in a scratch
  notes dir, `POST /api/record/start {"title": "Roadmap sync"}` against a
  scratch-port daemon… **stop**: that opens the mic. Instead verify through
  the watch path with `simulate`? Simulate doesn't take a title. The
  mic-free behavioral check is: unit-test coverage above **plus** driving
  `find_brief` via a tiny script against a real notes dir, plus confirming
  the UI card renders by injecting the event (`handleEvent` is reachable
  from the console: temporarily via a WS test or by calling the daemon's
  `_sink` from a `POST /api/simulate` run in a notes dir where a prior
  "Simulation" note exists — simulate titles are `Simulation {ts}`, which
  *will* FTS-match earlier Simulation notes; use that: run simulate twice,
  the second run should emit a brief). Wire `start_simulate` to also do the
  brief lookup (same guard: it has a real-ish title) precisely so this
  mic-free path exists.

---

## Milestone F — Follow-up drafts

**Goal:** "Draft follow-up email" for any saved note — local LLM, output to
a copy-me modal / clipboard, never sent anywhere.

### F1. New module `followup.py`

```python
"""Follow-up email drafts from a saved note — local Ollama, clipboard-bound.
The draft is returned to the caller; nothing is ever sent anywhere."""

def draft_followup(note_md: str, model: str = summarize.DEFAULT_MODEL) -> str
```

- Strip frontmatter (`notes.split_frontmatter`), cut everything from
  `^## Transcript$` on (the summary sections are the material; the raw
  transcript is noise and blows the budget), clamp to 20,000 chars.
- One `summarize._chat` call. System prompt: write a follow-up email from
  the note-taker to the other attendees; start with `Subject: ` on the
  first line; short recap, decisions, action items with owners/dues,
  friendly closing; only facts from the note — never invent recipients,
  dates, or commitments; plain text, no markdown.

### F2. Endpoints + CLI + UI

- `server.py`: `POST /api/notes/{name}/followup` → uses
  `_writable_note_path(name)` (read-only endpoint, but the live-journal 409
  is the right UX — a mid-recording journal has no summary to draft from)
  → `{"draft": ...}`; `OllamaError` → 503.
- `cli.py`: `wtm draft NOTE.md [--notes-dir] [--ollama-model]` → prints the
  draft to stdout (pipeable to `pbcopy`).
- UI: Export menu gains `<button id="followup-btn" class="export-item">Draft
  follow-up email…</button>` (always visible — it's local). Click → close
  menu, toast "Drafting…", POST, then open a modal overlay (new markup in
  index.html: `#draft-modal`, a `<textarea readonly>`, Copy and Close
  buttons). **Do not auto-write the clipboard after the fetch** — Safari
  drops the user-gesture context across awaits and the write fails
  silently; the modal's Copy button is a fresh gesture and works
  everywhere. Copy button → `navigator.clipboard.writeText` → toast
  "Copied — nothing was sent anywhere."
- Modal styling matches the app; Escape and backdrop-click close it.

### F3. Tests + verification

- `tests/test_followup.py`: prompt excludes the transcript and the
  frontmatter; clamping; monkeypatched `_chat` round-trip.
- API: happy path (monkeypatch `followup.draft_followup`), live-journal
  409, `OllamaError` → 503.
- Behavioral: `uv run wtm draft <a real note>` with Ollama running →
  plausible email; in the UI, draft from a note and copy from the modal.

---

## After all milestones

1. Full pass of the Definition of done from CLAUDE.md over the combined
   diff, plus the standing privacy audit: `grep -rn "http" src/` and
   confirm every hit is `localhost:11434`, `127.0.0.1`, or the pre-existing
   sanctioned `api.notion.com` path.
2. Update `CLAUDE.md`: Architecture entries for `templates.py`, `chat.py`,
   `diarize.py`, `briefs.py`, `followup.py`; new commands (`wtm ask`,
   `wtm draft`, `wtm templates`, `--template`, `--user-notes`, `--diarize`);
   a Sharp-edge note for whatever actually bit you (there will be
   something — record it honestly).
3. Tick Phase 4 items in `ROADMAP.md` (with the same style of parenthetical
   caveats the Phase 3 Notion entry uses — e.g. diarization "beta, tuned on
   synthetic voices only").
4. Anything you could not verify (real-meeting diarization quality, chat
   answer quality on a large corpus) is reported as **unverified**, not
   assumed working.
