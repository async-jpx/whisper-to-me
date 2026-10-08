"""Local-only HTTP/WebSocket daemon exposing the recording pipeline to a UI.

Binds 127.0.0.1 — hard-coded, never configurable — so nothing here is ever
reachable off the machine. All the actual work (Whisper, Ollama) already runs
locally elsewhere in the app; this module just gives a UI a way to drive it
instead of a terminal.

Only one session (manual record / accepted prompt / simulate) runs at a time:
the Whisper model is loaded once, lazily, on first use and reused after that. Every
session's events (transcript lines, echo-filter counts, summarizing/saved/
error notices, status changes) fan out to every connected WebSocket client
over a small per-client queue, so a slow or dead client drops events instead
of ever blocking the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import queue
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audio_store
from . import briefs, chat, coaching, export, followup, notes, notion_export, runner, search, templates
from . import summarize as summ
from . import watch
from .config import Config, load_config, save_config
from .runner import Active, Idle, Prompting, Refused, State
from .session import (
    console,
    load_transcriber,
    record_session,
    simulate_session,
    summarize_and_save,
)

STATIC_DIR = Path(__file__).with_name("static")

# CLI-only extras (see session.py) stripped before events hit the wire:
# "summary" on `saved` lets ConsoleSink print the note body (clients fetch it
# via GET /api/notes/{name}); "label" on `line` tells us whether the session
# has >1 source — the contract wants speaker=null when it doesn't, while the
# CLI keeps printing the speaker either way.
_WIRE_EXCLUDE = {"summary", "label"}

# How often the daemon purges recordings nobody played for 30 days.
AUDIO_PURGE_INTERVAL_S = 3600.0


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
    prompt_timeout: float = runner.PROMPT_TIMEOUT_S


class BusyError(RuntimeError):
    pass


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


class MeetingProbe(Protocol):
    def detect(self) -> runner.Trigger | None: ...

    def title_hint(self, trigger: runner.Trigger) -> str | None: ...

    def meeting_app(self, trigger: runner.Trigger | None) -> str | None: ...


class SystemProbe:
    def detect(self) -> runner.Trigger | None:
        return watch.detect_meeting(load_config().ignored_apps)

    def title_hint(self, trigger: runner.Trigger) -> str | None:
        return watch.meeting_title_hint(trigger)

    def meeting_app(self, trigger: runner.Trigger | None) -> str | None:
        if trigger is None:  # manual start: whoever holds the mic may be dictation
            return "Zoom" if watch.zoom_meeting_active() else None
        return "Zoom" if trigger == "zoom" else watch.mic_app_name(
            ignored=load_config().ignored_apps
        )


def status_wire(state: State, now_mono: float, now: datetime, timeout_s: float) -> dict:
    frame: dict = {
        "type": "status",
        "state": "idle",
        "origin": None,
        "title": None,
        "started": None,
        "elapsed_s": None,
        "prompt": None,
    }
    match state:
        case Prompting(prompt=prompt):
            frame["state"] = "prompting"
            frame["prompt"] = {
                "id": prompt.id,
                "title": prompt.title,
                "trigger": prompt.trigger,
                "app": prompt.app,
                "expires_in_s": round(max(0.0, prompt.deadline - now_mono), 1),
                "timeout_s": timeout_s,
            }
        case Active():
            frame["state"] = state.phase
            frame["origin"] = state.plan.origin
            frame["title"] = state.plan.title
            frame["started"] = state.started.isoformat()
            frame["elapsed_s"] = (now - state.started).total_seconds()
    return frame


class SessionManager:
    def __init__(self, opts: ServerOptions, probe: MeetingProbe | None = None) -> None:
        self.opts = opts
        self._probe = probe if probe is not None else SystemProbe()
        self._state: State = Idle()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._detector: threading.Thread | None = None
        self._audio_janitor: threading.Thread | None = None
        self._session_thread: threading.Thread | None = None
        self._transcriber = None
        self._transcriber_lock = threading.Lock()
        self._transcriber_warmup_started = False
        self._previewer = None
        self._previewer_lock = threading.Lock()
        self._previewer_warmup_started = False
        self._clients: list[_Client] = []
        self._clients_lock = threading.Lock()
        self._lines: list[dict] = []  # replay buffer for late-joining clients
        self._partials: dict[str, dict] = {}
        self._scratchpad: str = ""  # note-taker's live notes (Phase 4.2)

    def _get_transcriber(self):
        with self._transcriber_lock:
            if self._transcriber is None:
                self._transcriber = load_transcriber(self.opts.model, self.opts.language)
            return self._transcriber

    def _get_previewer(self):
        with self._previewer_lock:
            if self._previewer is None:
                if self.opts.model == "tiny":
                    self._previewer = self._get_transcriber()
                    return self._previewer
                try:
                    self._previewer = load_transcriber("tiny", self.opts.language)
                except Exception as exc:
                    console.print(f"[yellow]Tiny preview model unavailable: {exc}[/yellow]")
                    self._previewer = self._get_transcriber()
            return self._previewer

    def warm_transcriber(self) -> None:
        """Load Whisper while the UI is opening, before Record is pressed.

        Model construction can take several seconds on a cold process.  It is
        deliberately asynchronous: serving the UI and meeting detection stay
        responsive, while the first recording normally finds a ready model.
        """
        with self._lock:
            warm_preview = self._previewer is None and not self._previewer_warmup_started
            if warm_preview:
                self._previewer_warmup_started = True
        if warm_preview:
            threading.Thread(target=self._warm_previewer, daemon=True).start()
        with self._transcriber_lock:
            if self._transcriber is not None or self._transcriber_warmup_started:
                return
            self._transcriber_warmup_started = True

        def warm() -> None:
            try:
                self._get_transcriber()
            except Exception as exc:
                # Recording retains its normal error path and can retry later.
                console.print(f"[yellow]Whisper warm-up failed: {exc}[/yellow]")
            finally:
                with self._transcriber_lock:
                    self._transcriber_warmup_started = False

        threading.Thread(target=warm, daemon=True).start()

    def _warm_previewer(self) -> None:
        try:
            self._get_previewer()
        except Exception as exc:
            console.print(f"[yellow]Live preview warm-up failed: {exc}[/yellow]")
        finally:
            with self._lock:
                self._previewer_warmup_started = False

    def live_note_name(self) -> str | None:
        """Filename of the live journal while a session is writing it — the
        one note the edit endpoints must never touch (see notes.py invariant:
        the journal and the final rewrite target the same path)."""
        with self._lock:
            state = self._state
        if not isinstance(state, Active):
            return None
        return notes.note_path(state.plan.title, state.started, self.opts.notes_dir).name

    # -- scratchpad (Phase 4.2): the note-taker's live notes, which guide the
    # final summary. Persisted to a sidecar file (outside the *.md glob, so it
    # is never a note, never indexed, never reachable via _safe_note_path) so a
    # daemon crash mid-meeting can't lose what was typed.

    SCRATCHPAD_FILE = ".wtm-scratchpad.txt"

    def _scratchpad_path(self) -> Path:
        return self.opts.notes_dir / self.SCRATCHPAD_FILE

    def set_scratchpad(self, text: str) -> None:
        with self._lock:
            if not isinstance(self._state, Active):
                raise BusyError("no active session")
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

    def _status_locked(self) -> dict:
        return status_wire(
            self._state, time.monotonic(), datetime.now(), self.opts.prompt_timeout
        )

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def _transition(self, fn: Callable[[State], object]) -> object:
        with self._lock:
            result = fn(self._state)
            if isinstance(result, State) and result != self._state:
                if isinstance(result, Active) and not isinstance(self._state, Active):
                    self._lines = []
                    self._partials = {}
                    self._scratchpad = ""
                elif isinstance(self._state, Active) and not isinstance(result, Active):
                    self._partials = {}
                self._state = result
                frame = self._status_locked()
                with self._clients_lock:
                    for client in self._clients:
                        client.send(frame)
        return result

    def _broadcast(self, event: dict) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            client.send(event)

    def _sink(self, event: dict) -> None:
        if event.get("type") == "status":
            started = datetime.fromisoformat(event["started"])
            self._transition(lambda s: runner.recording_started(s, started))
            return
        wire = {k: v for k, v in event.items() if k not in _WIRE_EXCLUDE}
        if wire.get("type") in {"line", "partial"}:
            if not event.get("label"):
                wire["speaker"] = None  # single source: no speaker labels
        if wire.get("type") in {"partial", "partial_clear"}:
            with self._clients_lock:
                source = wire["source"]
                if wire["type"] == "partial":
                    self._partials[source] = wire
                elif self._partials.get(source, {}).get("id") == wire["id"]:
                    self._partials.pop(source, None)
                for client in self._clients:
                    client.send(wire)
            return
        if wire.get("type") == "line":
            with self._clients_lock:
                self._lines.append(wire)
                for client in self._clients:
                    client.send(wire)
            return
        self._broadcast(wire)

    def add_client(self) -> _Client:
        client = _Client()
        with self._lock:
            with self._clients_lock:
                self._clients.append(client)
                client.send(self._status_locked())
                for line in self._lines:
                    client.send(line)
                for partial in self._partials.values():
                    client.send(partial)
        return client

    def remove_client(self, client: _Client) -> None:
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)

    def open(self) -> None:
        if self._detector is None:
            self._detector = threading.Thread(target=self._detect_loop, daemon=True)
            self._detector.start()
        if self._audio_janitor is None:
            self._audio_janitor = threading.Thread(target=self._audio_janitor_loop, daemon=True)
            self._audio_janitor.start()

    def _audio_janitor_loop(self) -> None:
        """Startup + hourly: drop temp tracks of crashed sessions and kept
        recordings nobody played for audio_store.UNPLAYED_DAYS."""
        notes_dir = self.opts.notes_dir
        while True:
            try:
                audio_store.clean_temp(notes_dir)
                audio_store.purge_unplayed([notes_dir, notes.archive_dir(notes_dir)])
            except OSError as exc:
                console.print(f"[red]Audio cleanup failed: {exc}[/red]")
            if self._closing.wait(AUDIO_PURGE_INTERVAL_S):
                return

    def close(self) -> None:
        self._closing.set()
        self._wake.set()
        if self._detector is not None:
            self._detector.join()
        if self._audio_janitor is not None:
            self._audio_janitor.join()
        result = self._transition(runner.stop)
        if isinstance(result, Active):
            result.stop.set()
        if self._session_thread is not None:
            self._session_thread.join()

    def _detect_loop(self) -> None:
        while not self._closing.is_set():
            with self._lock:
                state = self._state
            polled = False
            # While Active our own recorder holds the mic: nothing to detect.
            if not isinstance(state, Active):
                try:
                    self._poll_meeting()
                    polled = True
                except Exception as exc:
                    console.print(f"[red]Meeting detection failed: {exc}[/red]")
            with self._lock:
                state = self._state
            timeout = self.opts.poll
            if polled and isinstance(state, Prompting):
                timeout = min(timeout, max(0.0, state.prompt.deadline - time.monotonic()))
            self._wake.wait(timeout)
            self._wake.clear()

    def _poll_meeting(self) -> None:
        trigger = self._probe.detect()
        verdict = self._transition(lambda s: runner.observe(s, trigger, time.monotonic()))
        if not isinstance(verdict, runner.Ask):
            return
        # The calendar osascript can take seconds: fetch it outside the lock,
        # then open the prompt only if nothing else happened meanwhile.
        hint = self._probe.title_hint(verdict.trigger)
        app = self._probe.meeting_app(verdict.trigger)
        prompt_id = secrets.token_hex(6)
        opened = self._transition(
            lambda s: runner.open_prompt(
                s, prompt_id, verdict.trigger, hint, self.opts.template,
                time.monotonic(), datetime.now(), self.opts.prompt_timeout, app=app,
            )
        )
        if isinstance(opened, Prompting) and opened.prompt.id == prompt_id:
            self.warm_transcriber()

    def start_record(self, title: str | None, template: str | None = None) -> None:
        now = datetime.now()
        app = self._probe.meeting_app(None)
        plan = runner.manual_plan(title, template or self.opts.template, now, app=app)
        self._start(plan, now)

    def start_simulate(
        self, mic: str, system: str | None, no_summary: bool, template: str | None = None
    ) -> None:
        now = datetime.now()
        src = runner.SimulateSource(mic, system, no_summary)
        plan = runner.simulate_plan(src, template or self.opts.template, now)
        self._start(plan, now)

    def _start(self, plan: runner.SessionPlan, now: datetime) -> None:
        result = self._transition(lambda s: runner.start(s, plan, now))
        if isinstance(result, Refused):
            raise BusyError(result.reason)
        self._launch(result)

    def answer_prompt(
        self, prompt_id: str, choice: runner.Answer, ignore_app: bool = False
    ) -> None:
        with self._lock:
            state = self._state
        live = isinstance(state, Prompting) and state.prompt.id == prompt_id
        app = state.prompt.app if live else None
        result = self._transition(
            lambda s: runner.answer(s, prompt_id, choice, datetime.now(), time.monotonic())
        )
        if isinstance(result, Refused):
            raise BusyError(result.reason)
        if isinstance(result, Active):
            self._launch(result)
        if ignore_app and app:
            save_config(lambda cfg: {"ignored_apps": sorted(cfg.ignored_apps | {app})})

    def stop(self) -> None:
        result = self._transition(runner.stop)
        if isinstance(result, Refused):
            raise BusyError(result.reason)
        result.stop.set()

    def _launch(self, active: Active) -> None:
        self._scratchpad_path().unlink(missing_ok=True)  # drop any stale sidecar
        if active.plan.find_brief:
            brief = briefs.find_brief(self.opts.notes_dir, active.plan.title)
            if brief:
                self._sink({"type": "brief", **brief})

        def guarded() -> None:
            try:
                self._run(active)
            except Exception as exc:  # a dead session thread must not wedge the daemon
                self._sink({"type": "error", "message": str(exc)})
            finally:
                self._transition(runner.session_ended)

        self._session_thread = threading.Thread(target=guarded, daemon=True)
        self._session_thread.start()

    def _run(self, active: Active) -> None:
        plan, opts = active.plan, self.opts
        transcriber = self._get_transcriber()
        keep_audio = load_config().keep_audio
        if plan.simulate is not None:
            transcript_lines, started = simulate_session(
                transcriber,
                plan.title,
                opts.notes_dir,
                plan.simulate.mic,
                system_path=plan.simulate.system,
                keep_echoes=opts.keep_echoes,
                use_aec=opts.use_aec,
                diarize=opts.diarize,
                events=self._sink,
                keep_audio=keep_audio,
            )
        else:
            should_stop = (
                runner.meeting_end_condition(plan.trigger, opts.silence_timeout)
                if plan.trigger is not None
                else None
            )
            transcript_lines, started = record_session(
                transcriber,
                plan.title,
                opts.notes_dir,
                device=opts.device,
                system_device=opts.system_device,
                should_stop=should_stop,
                keep_echoes=opts.keep_echoes,
                use_aec=opts.use_aec,
                diarize=opts.diarize,
                started=active.started,
                events=self._sink,
                stop_event=active.stop,
                keep_audio=keep_audio,
                preview_factory=self._get_previewer,
            )
        self._transition(runner.summarizing)
        summarize_and_save(
            plan.title,
            transcript_lines,
            started,
            opts.notes_dir,
            ollama_model=opts.ollama_model,
            context=opts.context,
            no_summary=plan.simulate.no_summary if plan.simulate is not None else False,
            auto_title=plan.auto_title,
            user_notes=self.get_scratchpad(),
            template=plan.template,
            events=self._sink,
            app=plan.app,
        )
        self._clear_scratchpad()


class RecordStartBody(BaseModel):
    title: str | None = None
    template: str | None = None


class NoteContentBody(BaseModel):
    content: str


class TaskToggleBody(BaseModel):
    task_index: int
    checked: bool


class PromptAnswerBody(BaseModel):
    answer: runner.Answer
    ignore_app: bool = False  # never prompt again for the prompt's app


class SimulateBody(BaseModel):
    mic: str
    system: str | None = None
    no_summary: bool = False
    template: str | None = None


class ScratchpadBody(BaseModel):
    content: str


class TemplateCreateBody(BaseModel):
    name: str
    description: str = ""
    body: str


class TemplateFavoriteBody(BaseModel):
    favorite: bool


class DefaultTemplateBody(BaseModel):
    name: str | None


class ChatBody(BaseModel):
    question: str
    history: list[dict] = []


class ChatStreamBody(BaseModel):
    # AI SDK UIMessage list: {role, parts: [{type: "text", text}, ...]}.
    # Extra fields (id, trigger, messageId) are ignored by pydantic.
    messages: list[dict] = []


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _ui_message_turns(messages: list[dict]) -> list[dict]:
    """AI SDK UIMessages → [{role, content}] turns (text parts joined)."""
    turns = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = "\n".join(
            str(p.get("text", ""))
            for p in msg.get("parts", [])
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
        if text:
            turns.append({"role": role, "content": text})
    return turns


class ObsidianSettingsBody(BaseModel):
    vault: str


class RecordingSettingsBody(BaseModel):
    keep_audio: bool


class DetectionSettingsBody(BaseModel):
    ignored_apps: list[str]


class NotionSettingsBody(BaseModel):
    # token omitted/blank keeps an already-saved one (the UI never re-sends it).
    token: str | None = None
    database_id: str


def _note_meta(path: Path) -> dict:
    """One NoteMeta (see the web UI's api/types.ts)."""
    text = path.read_text(encoding="utf-8")
    lines = notes.parse_transcript(text)
    return {
        "name": path.name,
        "title": notes.note_title(path),
        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        "date": notes.meeting_date(text),
        "app": notes.frontmatter_fields(text).get("app") or None,
        "has_audio": audio_store.has_audio(path),
        "duration_s": lines[-1].t if lines else None,
    }


def _list_notes(notes_dir: Path) -> list[dict]:
    if not notes_dir.is_dir():
        return []
    entries = [_note_meta(path) for path in notes_dir.glob("*.md")]
    entries.sort(key=lambda e: e["modified"], reverse=True)
    return entries


def _list_archived(notes_dir: Path) -> list[dict]:
    archive = notes.archive_dir(notes_dir)
    if not archive.is_dir():
        return []
    entries = [_note_meta(path) for path in archive.glob("*.md")]
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


def create_app(opts: ServerOptions, probe: MeetingProbe | None = None) -> FastAPI:
    app = FastAPI()
    manager = SessionManager(opts, probe)
    app.state.manager = manager  # tests reach the session state through here

    @app.get("/static/prompt.html")
    def prompt_page():
        # no-cache: the overlay's WebKit otherwise keeps a heuristically-cached
        # copy across upgrades, and a stale page answers endpoints that no
        # longer exist. Registered before the /static mount so it wins.
        return FileResponse(STATIC_DIR / "prompt.html", headers={"Cache-Control": "no-cache"})

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.on_event("startup")
    async def _open() -> None:
        manager.open()

    @app.on_event("shutdown")
    def _close() -> None:
        manager.close()

    @app.get("/")
    def index():
        # The React UI (webui/, built by Vite into static/dist — see CLAUDE.md).
        # no-cache: the asset filenames are content-hashed, but this document
        # is not — a heuristically-cached stale index points at deleted
        # bundles and breaks the page until revalidation.
        manager.warm_transcriber()
        index_html = STATIC_DIR / "dist" / "index.html"
        if index_html.is_file():
            return FileResponse(index_html, headers={"Cache-Control": "no-cache"})
        return {"app": "whisper-to-me", "detail": "webui not built"}

    @app.get("/api/status")
    def get_status():
        return manager.status()

    def _validate_template(name: str | None) -> None:
        if name is not None and templates.load_template(name) is None:
            raise HTTPException(status_code=400, detail=f"unknown template: {name}")

    def _template_dto(t: templates.Template, cfg: Config) -> dict:
        default = cfg.default_template
        if default is None or templates.load_template(default) is None:
            default = "default"
        return {
            "name": t.name,
            "title": t.title,
            "description": t.description,
            "builtin": t.builtin,
            "favorite": t.name in cfg.favorite_templates,
            "is_default": t.name == default,
            "body": t.sections,
        }

    @app.get("/api/templates")
    def list_templates_endpoint():
        cfg = load_config()
        return [_template_dto(t, cfg) for t in templates.list_templates()]

    @app.post("/api/templates", status_code=201)
    def create_template_endpoint(body: TemplateCreateBody):
        try:
            created = templates.create_template(body.name, body.description, body.body)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _template_dto(created, load_config())

    @app.delete("/api/templates/{name}", status_code=204)
    def delete_template_endpoint(name: str):
        try:
            templates.delete_template(name)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=f"unknown template: {name}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.put("/api/templates/{name}/favorite")
    def favorite_template_endpoint(name: str, body: TemplateFavoriteBody):
        try:
            templates.set_favorite(name, body.favorite)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=f"unknown template: {name}") from exc
        return _template_dto(templates.load_template(name), load_config())

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
            manager.stop()
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/prompts/{prompt_id}", status_code=202)
    def answer_prompt(prompt_id: str, body: PromptAnswerBody):
        """Answer the live meeting prompt by id: record starts the detected
        meeting, dismiss skips it until its trigger clears. ignore_app also
        stops prompting for the prompt's app from now on."""
        try:
            manager.answer_prompt(prompt_id, body.answer, body.ignore_app)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail="prompt expired") from exc
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
        notes.delete_note(path)
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
        notes.delete_note(_archived_path(name))
        return {"ok": True}

    @app.get("/api/notes/{name}/transcript")
    def get_transcript(name: str):
        path = _safe_note_path(opts.notes_dir, name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="note not found")
        lines = notes.parse_transcript(path.read_text(encoding="utf-8"))
        return {"lines": [asdict(line) for line in lines]}

    app.include_router(coaching.router(
        opts.notes_dir, opts.ollama_model, lambda name: _safe_note_path(opts.notes_dir, name),
    ))

    def _note_audio(name: str) -> Path:
        """The note's path, 404 unless it has a kept recording."""
        path = _safe_note_path(opts.notes_dir, name)
        if path is None or not audio_store.has_audio(path):
            raise HTTPException(status_code=404, detail="no audio")
        return path

    @app.get("/api/notes/{name}/audio")
    def get_audio(name: str):
        # FileResponse answers Range requests with 206; WebKit's <audio>
        # won't play or seek a source that can't.
        path = _note_audio(name)
        audio_store.mark_played(path)
        return FileResponse(audio_store.audio_path(path), media_type="audio/mp4")

    @app.get("/api/notes/{name}/audio/peaks")
    def get_audio_peaks(name: str):
        peaks = audio_store.peaks_path(_note_audio(name))
        if not peaks.is_file():
            raise HTTPException(status_code=404, detail="no audio")
        return FileResponse(peaks, media_type="application/json")

    @app.delete("/api/notes/{name}/audio", status_code=204)
    def delete_audio(name: str):
        path = _safe_note_path(opts.notes_dir, name)
        if path is None:
            raise HTTPException(status_code=404, detail="note not found")
        audio_store.delete_audio(path)
        return Response(status_code=204)

    @app.get("/api/search")
    def search_endpoint(q: str = ""):
        return search.search(opts.notes_dir, q)

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

    @app.post("/api/chat/stream")
    def chat_stream_endpoint(body: ChatStreamBody):
        # Streaming twin of /api/chat, speaking the AI SDK UI message stream
        # protocol (SSE) for the web UI's useChat. Same local-only pipeline:
        # FTS retrieval here, token stream from the localhost Ollama.
        turns = _ui_message_turns(body.messages)
        if not turns or turns[-1]["role"] != "user":
            raise HTTPException(status_code=400, detail="no user question")
        question = turns[-1]["content"]
        history = turns[:-1]

        def gen():
            yield _sse({"type": "start"})
            yield _sse({"type": "text-start", "id": "answer"})
            prep = chat.prepare(opts.notes_dir, question, history=history)
            if prep is None:
                yield _sse({"type": "text-delta", "id": "answer", "delta": chat.NO_MATCH})
                sources: list[dict] = []
            else:
                user_prompt, used = prep
                pieces: list[str] = []
                try:
                    for piece in summ._chat_stream(
                        opts.ollama_model, chat.CHAT_SYSTEM, user_prompt
                    ):
                        pieces.append(piece)
                        yield _sse({"type": "text-delta", "id": "answer", "delta": piece})
                except summ.OllamaError as exc:
                    yield _sse({"type": "error", "errorText": str(exc)})
                    yield "data: [DONE]\n\n"
                    return
                sources = chat.cited_sources("".join(pieces), used)
            yield _sse({"type": "text-end", "id": "answer"})
            yield _sse({"type": "data-sources", "data": sources})
            yield _sse({"type": "finish"})
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "x-vercel-ai-ui-message-stream": "v1",
            },
        )

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
            "templates": {"default": cfg.default_template},
            "recording": {"keep_audio": cfg.keep_audio},
            "detection": {"ignored_apps": sorted(cfg.ignored_apps)},
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

    @app.put("/api/settings/recording")
    def set_recording(body: RecordingSettingsBody):
        save_config({"keep_audio": body.keep_audio})
        return _settings_state()

    @app.put("/api/settings/detection")
    def set_detection(body: DetectionSettingsBody):
        save_config({"ignored_apps": sorted({a.strip() for a in body.ignored_apps if a.strip()})})
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

    @app.put("/api/settings/default-template")
    def set_default_template(body: DefaultTemplateBody):
        try:
            templates.set_default(body.name)
        except LookupError as exc:
            raise HTTPException(status_code=400, detail=f"unknown template: {body.name}") from exc
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
        this from detection/record/summarize paths."""
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
