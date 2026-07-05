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

from . import notes
from . import summarize as summ
from .runner import WatchOptions, watch_loop
from .session import load_transcriber, record_session, simulate_session, summarize_and_save

STATIC_DIR = Path(__file__).with_name("static")

# "summary" is a CLI-only extra on the `saved` event (see session.py) so
# ConsoleSink can print the note body; the wire contract for real clients is
# just path/title/name, and a UI fetches the body via GET /api/notes/{name}.
_WIRE_EXCLUDE = {"summary"}


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

    def _get_transcriber(self):
        if self._transcriber is None:
            self._transcriber = load_transcriber(self.opts.model, self.opts.language)
        return self._transcriber

    def status(self) -> dict:
        elapsed = (
            (datetime.now() - self.started).total_seconds()
            if self.started and self.state != "idle"
            else None
        )
        return {
            "state": self.state,
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

    def start_record(self, title: str | None) -> None:
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
        self._broadcast_status()

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
                events=self._sink,
            )

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
        self._broadcast_status()
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
            )
            watch_loop(transcriber, opts, events=self._sink, stop_event=stop_event)

        self._run_in_background(run)

    def stop_watch(self) -> None:
        with self._lock:
            if self._mode != "watch" or self.state == "idle":
                raise BusyError(self.state)
            stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()

    def start_simulate(self, mic: str, system: str | None, no_summary: bool) -> None:
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
        self._broadcast_status()

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
                events=self._sink,
            )

        self._run_in_background(run)


class RecordStartBody(BaseModel):
    title: str | None = None


class SimulateBody(BaseModel):
    mic: str
    system: str | None = None
    no_summary: bool = False


def _list_notes(notes_dir: Path) -> list[dict]:
    if not notes_dir.is_dir():
        return []
    entries = [
        {
            "name": path.name,
            "title": _note_title(path),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }
        for path in notes_dir.glob("*.md")
    ]
    entries.sort(key=lambda e: e["modified"], reverse=True)
    return entries


def _note_title(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return path.stem


def _safe_note_path(notes_dir: Path, name: str) -> Path | None:
    """Resolve `name` under notes_dir and reject anything that escapes it —
    guards GET /api/notes/{name} against path traversal."""
    base = notes_dir.resolve()
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def create_app(opts: ServerOptions) -> FastAPI:
    app = FastAPI()
    manager = SessionManager(opts)

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

    @app.post("/api/record/start", status_code=202)
    def record_start(body: RecordStartBody = RecordStartBody()):
        try:
            manager.start_record(body.title)
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
        try:
            manager.start_simulate(body.mic, body.system, body.no_summary)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/api/notes")
    def list_notes():
        return _list_notes(opts.notes_dir)

    @app.get("/api/notes/{name}")
    def get_note(name: str):
        path = _safe_note_path(opts.notes_dir, name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="note not found")
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")

    @app.websocket("/api/events")
    async def events_ws(ws: WebSocket) -> None:
        await ws.accept()
        client = manager.add_client()
        try:
            while True:
                event = await asyncio.to_thread(client.queue.get)
                await ws.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            manager.remove_client(client)

    return app


def run_server(opts: ServerOptions, port: int = 8737, open_browser: bool = False) -> None:
    app = create_app(opts)

    if open_browser:
        @app.on_event("startup")
        async def _open_browser() -> None:
            webbrowser.open(f"http://127.0.0.1:{port}/")

    uvicorn.run(app, host="127.0.0.1", port=port)
