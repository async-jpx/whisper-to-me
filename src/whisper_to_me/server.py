"""Local-only HTTP/WebSocket daemon exposing the recording pipeline to a UI.

Binds 127.0.0.1 — hard-coded, never configurable — so nothing here is ever
reachable off the machine. All the actual work (Whisper, Ollama) already runs
locally elsewhere in the app; this module just gives a UI a way to drive it
instead of a terminal.

Only one session (record / watch / simulate) runs at a time: the Whisper
model is loaded once, lazily, on first use and reused after that. Every
session's events (transcript lines, echo-filter counts, summarizing/saved/
error notices, status changes) fan out to every connected WebSocket client
over a small per-client queue, so a slow or dead client drops events instead
of ever blocking the pipeline.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import briefs, chat, export, followup, notes, notion_export, search, templates
from . import summarize as summ
from .config import load_config, save_config
from .runner import WatchOptions, watch_loop
from .session import load_transcriber, record_session, simulate_session, summarize_and_save

STATIC_DIR = Path(__file__).with_name("static")

# CLI-only extras (see session.py) stripped before events hit the wire:
# "summary" on `saved` lets ConsoleSink print the note body (clients fetch it
# via GET /api/notes/{name}); "label" on `line` tells us whether the session
# has >1 source — the contract wants speaker=null when it doesn't, while the
# CLI keeps printing the speaker either way.
_WIRE_EXCLUDE = {"summary", "label"}


@dataclass
class ServerOptions:
    model: str = "large-v3-turbo"
    language: str | None = None
    ollama_model: str = summ.DEFAULT_MODEL
    notes_dir: Path = notes.DEFAULT_NOTES_DIR
    context: str = ""
    device: int | None = None
    system_device: str = "auto"
    keep_echoes: bool = False
    use_aec: bool = True
    poll: float = 3.0
    silence_timeout: float = 120.0
    template: str | None = None
    diarize: bool = False


class BusyError(RuntimeError):
    def __init__(self, state: str) -> None:
        super().__init__(f"busy: {state}")
        self.state = state


class _Client:
    """One connected WS client's outbound queue. Bounded so a stalled client
    can't back up memory or block the pipeline thread that's feeding it."""

    def __init__(self) -> None:
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=1000)

    def send(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            pass  # slow/dead client: drop rather than block the recorder


class SessionManager:
    """Owns the single active record/watch/simulate session and mirrors its
    status for GET /api/status and newly-connected WS clients."""

    def __init__(self, opts: ServerOptions) -> None:
        self.opts = opts
        self.state = "idle"  # idle | recording | watching | summarizing
        self.title: str | None = None
        self.started: datetime | None = None
        self._mode: str | None = None  # "record" | "watch" | "simulate"
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._transcriber = None
        self._clients: list[_Client] = []
        self._clients_lock = threading.Lock()
        self._lines: list[dict] = []  # replay buffer for late-joining clients
        self._scratchpad: str = ""  # note-taker's live notes (Phase 4.2)

    def _get_transcriber(self):
        if self._transcriber is None:
            self._transcriber = load_transcriber(self.opts.model, self.opts.language)
        return self._transcriber

    def live_note_name(self) -> str | None:
        """Filename of the live journal while a session is writing it — the
        one note the edit endpoints must never touch (see notes.py invariant:
        the journal and the final rewrite target the same path)."""
        with self._lock:
            if self.state == "idle" or not self.title or not self.started:
                return None
            return notes.note_path(self.title, self.started, self.opts.notes_dir).name

    # -- scratchpad (Phase 4.2): the note-taker's live notes, which guide the
    # final summary. Persisted to a sidecar file (outside the *.md glob, so it
    # is never a note, never indexed, never reachable via _safe_note_path) so a
    # daemon crash mid-meeting can't lose what was typed.

    SCRATCHPAD_FILE = ".wtm-scratchpad.txt"

    def _scratchpad_path(self) -> Path:
        return self.opts.notes_dir / self.SCRATCHPAD_FILE

    def set_scratchpad(self, text: str) -> None:
        with self._lock:
            if self.state == "idle":
                raise BusyError(self.state)
            self._scratchpad = text
            # Sidecar write stays under the lock so two racing PUTs can't
            # leave the file holding an older value than memory.
            self.opts.notes_dir.mkdir(parents=True, exist_ok=True)
            notes.write_note_text(self._scratchpad_path(), text)

    def get_scratchpad(self) -> str:
        with self._lock:
            return self._scratchpad

    def _clear_scratchpad(self) -> None:
        with self._lock:
            self._scratchpad = ""
        self._scratchpad_path().unlink(missing_ok=True)

    def status(self) -> dict:
        elapsed = (
            (datetime.now() - self.started).total_seconds()
            if self.started and self.state != "idle"
            else None
        )
        return {
            "state": self.state,
            "mode": self._mode,
            "title": self.title,
            "started": self.started.isoformat() if self.started else None,
            "elapsed_s": elapsed,
        }

    # -- event fanout ---------------------------------------------------

    def _broadcast(self, event: dict) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            client.send(event)

    def _broadcast_status(self) -> None:
        self._broadcast({"type": "status", **self.status()})

    def _sink(self, event: dict) -> None:
        """The event sink passed into record_session/summarize_and_save/
        watch_loop. `status` events from downstream update our own mirrored
        state (they're the authoritative source for title/started once a
        recording is under way) and are re-broadcast with elapsed_s added;
        everything else is forwarded as-is, minus CLI-only extras."""
        if event.get("type") == "status":
            self.state = event["state"]
            self.title = event.get("title")
            started = event.get("started")
            self.started = datetime.fromisoformat(started) if started else None
            self._broadcast_status()
            return
        wire = {k: v for k, v in event.items() if k not in _WIRE_EXCLUDE}
        if wire.get("type") == "line":
            if not event.get("label"):
                wire["speaker"] = None  # single source: no speaker labels
            self._lines.append(wire)
        self._broadcast(wire)

    def add_client(self) -> _Client:
        client = _Client()
        with self._clients_lock:
            self._clients.append(client)
        client.send({"type": "status", **self.status()})
        for line in self._lines:
            client.send(line)
        return client

    def remove_client(self, client: _Client) -> None:
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)

    # -- lifecycle --------------------------------------------------------

    def _reset_to_idle(self) -> None:
        with self._lock:
            self.state = "idle"
            self.title = None
            self.started = None
            self._mode = None
            self._stop_event = None
            self._thread = None
        self._broadcast_status()

    def _run_in_background(self, target) -> None:
        def guarded() -> None:
            try:
                target()
            except Exception as exc:  # a dead session thread must not wedge the daemon
                self._sink({"type": "error", "message": str(exc)})
            finally:
                self._reset_to_idle()

        self._thread = threading.Thread(target=guarded, daemon=True)
        self._thread.start()

    def start_record(self, title: str | None, template: str | None = None) -> None:
        chosen_template = template or self.opts.template
        with self._lock:
            if self.state != "idle":
                raise BusyError(self.state)
            started = datetime.now()
            final_title = title or f"Meeting {started:%d %b %H:%M}"
            self.title, self.started, self.state, self._mode = (
                final_title, started, "recording", "record",
            )
            self._stop_event = threading.Event()
            self._lines = []
            self._scratchpad = ""
        self._broadcast_status()
        self._scratchpad_path().unlink(missing_ok=True)  # drop any stale sidecar
        # Brief only for a user-supplied title — the "Meeting <time>" placeholder
        # can't match a prior meeting meaningfully.
        if title:
            brief = briefs.find_brief(self.opts.notes_dir, final_title)
            if brief:
                self._sink({"type": "brief", **brief})

        def run() -> None:
            transcriber = self._get_transcriber()
            transcript_lines, _ = record_session(
                transcriber,
                final_title,
                self.opts.notes_dir,
                device=self.opts.device,
                system_device=self.opts.system_device,
                keep_echoes=self.opts.keep_echoes,
                use_aec=self.opts.use_aec,
                diarize=self.opts.diarize,
                started=started,
                events=self._sink,
                stop_event=self._stop_event,
            )
            self.state = "summarizing"
            self._broadcast_status()
            summarize_and_save(
                final_title,
                transcript_lines,
                started,
                self.opts.notes_dir,
                ollama_model=self.opts.ollama_model,
                context=self.opts.context,
                auto_title=title is None,
                user_notes=self.get_scratchpad(),
                template=chosen_template,
                events=self._sink,
            )
            self._clear_scratchpad()

        self._run_in_background(run)

    def stop_record(self) -> None:
        with self._lock:
            if self._mode != "record" or self.state == "idle":
                raise BusyError(self.state)
            stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()

    def start_watch(self) -> None:
        with self._lock:
            if self.state != "idle":
                raise BusyError(self.state)
            self.title, self.started, self.state, self._mode = None, None, "watching", "watch"
            self._stop_event = threading.Event()
            self._lines = []
            self._scratchpad = ""
        self._broadcast_status()
        self._scratchpad_path().unlink(missing_ok=True)  # drop any stale sidecar
        stop_event = self._stop_event

        def run() -> None:
            transcriber = self._get_transcriber()
            opts = WatchOptions(
                title=None,
                device=self.opts.device,
                system_device=self.opts.system_device,
                keep_echoes=self.opts.keep_echoes,
                use_aec=self.opts.use_aec,
                poll=self.opts.poll,
                silence_timeout=self.opts.silence_timeout,
                notes_dir=self.opts.notes_dir,
                ollama_model=self.opts.ollama_model,
                context=self.opts.context,
                no_summary=False,
                template=self.opts.template,
                diarize=self.opts.diarize,
            )
            watch_loop(
                transcriber,
                opts,
                events=self._sink,
                stop_event=stop_event,
                scratchpad=self.get_scratchpad,
                clear_scratchpad=self._clear_scratchpad,
            )

        self._run_in_background(run)

    def stop_watch(self) -> None:
        with self._lock:
            if self._mode != "watch" or self.state == "idle":
                raise BusyError(self.state)
            stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()

    def start_simulate(
        self, mic: str, system: str | None, no_summary: bool, template: str | None = None
    ) -> None:
        chosen_template = template or self.opts.template
        with self._lock:
            if self.state != "idle":
                raise BusyError(self.state)
            started = datetime.now()
            final_title = f"Simulation {started:%d %b %H:%M}"
            self.title, self.started, self.state, self._mode = (
                final_title, started, "recording", "simulate",
            )
            self._stop_event = threading.Event()
            self._lines = []
            self._scratchpad = ""
        self._broadcast_status()
        self._scratchpad_path().unlink(missing_ok=True)  # drop any stale sidecar
        # Simulate has a real-ish title ("Simulation <time>"), so it exercises
        # the brief path mic-free: a second run matches the first run's note.
        brief = briefs.find_brief(self.opts.notes_dir, final_title)
        if brief:
            self._sink({"type": "brief", **brief})

        def run() -> None:
            transcriber = self._get_transcriber()
            transcript_lines, _ = simulate_session(
                transcriber,
                final_title,
                self.opts.notes_dir,
                mic,
                system_path=system,
                keep_echoes=self.opts.keep_echoes,
                use_aec=self.opts.use_aec,
                diarize=self.opts.diarize,
                events=self._sink,
            )
            self.state = "summarizing"
            self._broadcast_status()
            summarize_and_save(
                final_title,
                transcript_lines,
                started,
                self.opts.notes_dir,
                ollama_model=self.opts.ollama_model,
                context=self.opts.context,
                no_summary=no_summary,
                auto_title=True,
                user_notes=self.get_scratchpad(),
                template=chosen_template,
                events=self._sink,
            )
            self._clear_scratchpad()

        self._run_in_background(run)


class RecordStartBody(BaseModel):
    title: str | None = None
    template: str | None = None


class NoteContentBody(BaseModel):
    content: str


class TaskToggleBody(BaseModel):
    task_index: int
    checked: bool


class SimulateBody(BaseModel):
    mic: str
    system: str | None = None
    no_summary: bool = False
    template: str | None = None


class ScratchpadBody(BaseModel):
    content: str


class ChatBody(BaseModel):
    question: str
    history: list[dict] = []


class ObsidianSettingsBody(BaseModel):
    vault: str


class NotionSettingsBody(BaseModel):
    # token omitted/blank keeps an already-saved one (the UI never re-sends it).
    token: str | None = None
    database_id: str


def _list_notes(notes_dir: Path) -> list[dict]:
    if not notes_dir.is_dir():
        return []
    entries = [
        {
            "name": path.name,
            "title": notes.note_title(path),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }
        for path in notes_dir.glob("*.md")
    ]
    entries.sort(key=lambda e: e["modified"], reverse=True)
    return entries


def _list_archived(notes_dir: Path) -> list[dict]:
    archive = notes.archive_dir(notes_dir)
    if not archive.is_dir():
        return []
    entries = [
        {
            "name": path.name,
            "title": notes.note_title(path),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }
        for path in archive.glob("*.md")
    ]
    entries.sort(key=lambda e: e["modified"], reverse=True)
    return entries


def _resolve_md(base: Path, name: str) -> Path | None:
    """Resolve `name` under `base` and reject anything that escapes it. Only
    `.md` files qualify: everything else (the search index, editor temp files)
    is not a note and must stay unreachable, especially from write/delete."""
    if not name.endswith(".md"):
        return None
    base = base.resolve()
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _safe_note_path(notes_dir: Path, name: str) -> Path | None:
    """Guards the /api/notes/{name} endpoints against path traversal."""
    return _resolve_md(notes_dir, name)


def _safe_archived_path(notes_dir: Path, name: str) -> Path | None:
    """Same guard as _safe_note_path, rooted at the Archive subfolder."""
    return _resolve_md(notes.archive_dir(notes_dir), name)


def create_app(opts: ServerOptions) -> FastAPI:
    app = FastAPI()
    manager = SessionManager(opts)
    app.state.manager = manager  # tests reach the session state through here

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index():
        index_html = STATIC_DIR / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)
        return {"app": "whisper-to-me"}

    @app.get("/api/status")
    def get_status():
        return manager.status()

    def _validate_template(name: str | None) -> None:
        if name is not None and templates.load_template(name) is None:
            raise HTTPException(status_code=400, detail=f"unknown template: {name}")

    @app.get("/api/templates")
    def list_templates_endpoint():
        return [
            {"name": t.name, "description": t.description, "builtin": t.builtin}
            for t in templates.list_templates()
        ]

    @app.post("/api/record/start", status_code=202)
    def record_start(body: RecordStartBody = RecordStartBody()):
        _validate_template(body.template)
        try:
            manager.start_record(body.title, body.template)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/record/stop", status_code=202)
    def record_stop():
        try:
            manager.stop_record()
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/watch/start", status_code=202)
    def watch_start():
        try:
            manager.start_watch()
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/watch/stop", status_code=202)
    def watch_stop():
        try:
            manager.stop_watch()
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/simulate", status_code=202)
    def simulate(body: SimulateBody):
        # Validate up front: a bad path reaching FileRecorder._pump_loop
        # (audio.py) raises inside its own daemon thread, which never signals
        # the chunker thread to stop — the session would hang forever with no
        # stop endpoint to recover it (see runner.py/session.py notes).
        for label, value in (("mic", body.mic), ("system", body.system)):
            if value is not None and not Path(value).is_file():
                raise HTTPException(status_code=400, detail=f"{label} file not found: {value}")
        _validate_template(body.template)
        try:
            manager.start_simulate(body.mic, body.system, body.no_summary, body.template)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.put("/api/session/scratchpad")
    def put_scratchpad(body: ScratchpadBody):
        # Cap the size so a runaway client can't grow daemon memory / the
        # sidecar file unbounded.
        if len(body.content) > 100_000:
            raise HTTPException(status_code=413, detail="scratchpad too large")
        try:
            manager.set_scratchpad(body.content)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail="no active session") from exc
        return {"ok": True}

    @app.get("/api/session/scratchpad")
    def get_scratchpad():
        return {"content": manager.get_scratchpad()}

    @app.get("/api/notes")
    def list_notes():
        return _list_notes(opts.notes_dir)

    @app.get("/api/notes/{name}")
    def get_note(name: str):
        path = _safe_note_path(opts.notes_dir, name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="note not found")
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")

    def _writable_note_path(name: str) -> Path:
        """Shared guard for the write endpoints: must exist, must not escape
        the notes dir, must not be the live session's journal (a concurrent
        rewrite there would race append_line/save_note and lose lines)."""
        path = _safe_note_path(opts.notes_dir, name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="note not found")
        if name == manager.live_note_name():
            raise HTTPException(status_code=409, detail="note is being recorded")
        return path

    @app.put("/api/notes/{name}")
    def put_note(name: str, body: NoteContentBody):
        path = _writable_note_path(name)
        notes.write_note_text(path, body.content)
        return {"ok": True, "title": notes.note_title(path)}

    @app.patch("/api/notes/{name}")
    def patch_note(name: str, body: TaskToggleBody):
        path = _writable_note_path(name)
        if body.task_index < 0 or not notes.toggle_task(path, body.task_index, body.checked):
            raise HTTPException(status_code=400, detail="no such task item")
        return {"ok": True}

    @app.delete("/api/notes/{name}")
    def delete_note(name: str):
        # Same live-journal guard as the write endpoints: never delete the note
        # a session is still appending transcript lines to.
        path = _writable_note_path(name)
        path.unlink()
        return {"ok": True}

    @app.post("/api/notes/{name}/archive")
    def archive_note(name: str):
        path = _writable_note_path(name)  # live-journal guard: don't move a live note
        dest = notes.move_note(path, notes.archive_dir(opts.notes_dir))
        return {"ok": True, "name": dest.name}

    @app.get("/api/archived")
    def list_archived():
        return _list_archived(opts.notes_dir)

    def _archived_path(name: str) -> Path:
        path = _safe_archived_path(opts.notes_dir, name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="note not found")
        return path

    @app.post("/api/archived/{name}/restore")
    def restore_note(name: str):
        path = _archived_path(name)
        dest = notes.move_note(path, opts.notes_dir)
        return {"ok": True, "name": dest.name}

    @app.delete("/api/archived/{name}")
    def delete_archived(name: str):
        _archived_path(name).unlink()
        return {"ok": True}

    @app.get("/api/search")
    def search_endpoint(q: str = ""):
        return search.search_notes(opts.notes_dir, q)

    @app.post("/api/chat")
    def chat_endpoint(body: ChatBody):
        # Independent of the session state machine: asking questions while idle,
        # recording, or summarizing are all fine. A sync def runs in FastAPI's
        # threadpool, so a slow Ollama answer never blocks the event loop or the
        # WebSocket fan-out; it merely queues behind any in-flight summarize.
        q = body.question.strip()
        if not q:
            raise HTTPException(status_code=400, detail="empty question")
        try:
            return chat.answer_question(
                opts.notes_dir, q, model=opts.ollama_model, history=body.history
            )
        except summ.OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # -- exports (Phase 3). Config is re-read per request so pasting a vault
    # path or Notion token into config.toml needs no daemon restart.

    @app.get("/api/export/config")
    def export_config():
        cfg = load_config()
        return {
            "obsidian_vault": str(cfg.obsidian_vault) if cfg.obsidian_vault else None,
            "notion_configured": cfg.notion_configured,  # never the token itself
        }

    # -- connections (Settings UI). These write config.toml so users connect
    # Obsidian / Notion from the UI instead of hand-editing TOML. Purely local
    # disk I/O: saving a Notion token here adds NO network path — the token is
    # only ever sent by the sanctioned per-note push (/api/notes/{name}/notion).

    def _settings_state() -> dict:
        cfg = load_config()
        return {
            "obsidian_vault": str(cfg.obsidian_vault) if cfg.obsidian_vault else None,
            "notion_configured": cfg.notion_configured,
            "notion_database_id": cfg.notion_database_id,  # id is not a secret
            "notion_token_set": bool(cfg.notion_token),    # never the token itself
        }

    @app.get("/api/settings")
    def get_settings():
        return _settings_state()

    @app.put("/api/settings/obsidian")
    def connect_obsidian(body: ObsidianSettingsBody):
        if not body.vault.strip():
            raise HTTPException(status_code=400, detail="vault path is required")
        save_config({"obsidian_vault": body.vault})
        return _settings_state()

    @app.delete("/api/settings/obsidian")
    def disconnect_obsidian():
        save_config({"obsidian_vault": None})
        return _settings_state()

    @app.put("/api/settings/notion")
    def connect_notion(body: NotionSettingsBody):
        # notion_configured (and every push) needs the token + database_id pair.
        if not body.database_id.strip():
            raise HTTPException(status_code=400, detail="database_id is required")
        token = (body.token or "").strip() or load_config().notion_token
        if not token:
            raise HTTPException(
                status_code=400, detail="a Notion integration token is required"
            )
        # No network verification here on purpose: the only code allowed to reach
        # api.notion.com is the per-note push. Credentials are checked on first push.
        save_config({"notion_token": token, "notion_database_id": body.database_id})
        return _settings_state()

    @app.delete("/api/settings/notion")
    def disconnect_notion():
        save_config({"notion_token": None, "notion_database_id": None})
        return _settings_state()

    @app.post("/api/notes/{name}/vault")
    def copy_note_to_vault(name: str):
        path = _writable_note_path(name)  # live-journal guard: no partial copies
        cfg = load_config()
        if cfg.obsidian_vault is None:
            raise HTTPException(status_code=400, detail="no vault configured")
        dest = export.copy_to_vault(path, cfg.obsidian_vault, overwrite=True)
        return {"ok": True, "path": str(dest)}

    @app.post("/api/notes/{name}/followup")
    def draft_followup_endpoint(name: str):
        # Read-only, but the live-journal 409 is the right UX: a mid-recording
        # journal has no summary to draft from.
        path = _writable_note_path(name)
        try:
            draft = followup.draft_followup(
                path.read_text(encoding="utf-8"), model=opts.ollama_model
            )
        except summ.OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"draft": draft}

    @app.post("/api/notes/{name}/notion")
    def push_note_to_notion(name: str):
        """The one sanctioned network export — fires only from an explicit,
        per-note user action in the UI (which confirms first). Never call
        this from watch/record/summarize paths."""
        path = _writable_note_path(name)
        cfg = load_config()
        if not cfg.notion_configured:
            raise HTTPException(status_code=400, detail="Notion is not configured")
        try:
            url = notion_export.push_note(path, cfg.notion_token, cfg.notion_database_id)
        except notion_export.NotionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "url": url}

    @app.websocket("/api/events")
    async def events_ws(ws: WebSocket) -> None:
        await ws.accept()
        client = manager.add_client()

        async def sender() -> None:
            while True:
                try:
                    # The 1s timeout bounds how long a cancelled sender's
                    # executor thread lingers in queue.get — an untimed get
                    # would pin one ThreadPoolExecutor slot per dead client
                    # until the next event, eventually starving all clients.
                    event = await asyncio.to_thread(client.queue.get, True, 1.0)
                except queue.Empty:
                    continue
                await ws.send_json(event)

        sender_task = asyncio.create_task(sender())
        try:
            while True:
                # We never expect client messages; this read exists to notice
                # a disconnect immediately instead of on the next failed send.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            sender_task.cancel()
            manager.remove_client(client)

    return app


def run_server(opts: ServerOptions, port: int = 8737, open_browser: bool = False) -> None:
    app = create_app(opts)

    if open_browser:
        @app.on_event("startup")
        async def _open_browser() -> None:
            webbrowser.open(f"http://127.0.0.1:{port}/")

    uvicorn.run(app, host="127.0.0.1", port=port)
