"""Recording-session orchestration: capture sources, live transcription
workers, and the summarize-and-save step shared by `record` and `watch`."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from rich.console import Console

from . import audio, notes
from . import summarize as summ

console = Console()

StopCondition = Callable[[list[audio.Recorder]], bool]


def load_transcriber(model: str, language: str | None):
    from .transcribe import Transcriber  # deferred: heavy import

    with console.status(f"Loading Whisper model '{model}' (downloads once, then offline)…"):
        return Transcriber(model_size=model, language=language)


def _system_audio_sources(system_device: str) -> list[tuple[str, audio.Recorder]]:
    """Build 'Others' sources: native system tap, else loopback devices."""
    if system_device == "off":
        return []

    if system_device != "auto":  # explicit device index forces that device
        idx = int(system_device)
        dev = next((d for d in audio.list_devices() if d["index"] == idx), None)
        if dev is None:
            console.print(f"[red]No input device with index {idx}.[/red]")
            return []
        console.print(f"[dim]Capturing call audio from '{dev['name']}'.[/dim]")
        return [("Others", audio.Recorder(device=idx))]

    tap_binary = audio.build_system_tap()
    if tap_binary is not None:
        console.print(
            "[dim]Capturing system audio natively — remote participants in any "
            "app (Zoom, Teams, Meet…) are transcribed. macOS may ask for "
            "System Audio Recording permission once.[/dim]"
        )
        return [("Others", audio.SystemAudioTap(tap_binary))]

    loopbacks = audio.find_loopback_devices()
    for dev in loopbacks:
        console.print(f"[dim]Capturing call audio from '{dev['name']}' (fallback).[/dim]")
    if not loopbacks:
        console.print(
            "[dim]No system-audio capture available — only the mic will be "
            "heard. Install Xcode Command Line Tools (for the native tap) or "
            "pass --system-device.[/dim]"
        )
    return [("Others", audio.Recorder(device=d["index"])) for d in loopbacks]


def record_session(
    transcriber,
    title: str,
    notes_dir: Path,
    device: int | None = None,
    system_device: str = "auto",
    should_stop: StopCondition | None = None,
) -> tuple[list[tuple[str, str]], datetime]:
    """Record (mic + system audio) until Ctrl-C or should_stop(recorders).

    Returns (transcript_lines, started); lines are (stamp, text) sorted by
    capture time, with speaker labels when there is more than one source.
    """
    sources: list[tuple[str, audio.Recorder]] = [("You", audio.Recorder(device=device))]
    sources += _system_audio_sources(system_device)

    started = datetime.now()
    raw_lines: list[tuple[datetime, str, str]] = []  # (capture_time, speaker, text)
    label = len(sources) > 1
    # Live journal: every line lands on disk immediately, so a crash or kill
    # can never lose the transcript. summarize_and_save rewrites this file
    # with the time-sorted transcript and the summary at the end.
    live_path = notes.start_live_note(title, started, notes_dir)

    def make_worker(speaker: str, recorder: audio.Recorder) -> threading.Thread:
        def worker() -> None:
            while True:
                item = recorder.chunks.get()
                if item is None:
                    return
                captured_at, chunk = item
                text = transcriber.transcribe_chunk(chunk)
                if text:
                    stamp = str(captured_at - started).split(".")[0]
                    line = f"**{speaker}:** {text}" if label else text
                    raw_lines.append((captured_at, speaker, text))
                    notes.append_line(live_path, stamp, line)
                    console.print(f"[dim][{stamp}][/dim] [bold]{speaker}:[/bold] {text}")

        return threading.Thread(target=worker)

    workers = [make_worker(speaker, rec) for speaker, rec in sources]
    for _, rec in sources:
        rec.start()
    for w in workers:
        w.start()

    recorders = [rec for _, rec in sources]
    try:
        while any(w.is_alive() for w in workers):
            workers[0].join(timeout=0.5)
            if should_stop is not None and should_stop(recorders):
                break
    except KeyboardInterrupt:
        pass
    console.print("\n[yellow]Stopping… transcribing remaining audio.[/yellow]")
    for _, rec in sources:
        rec.stop()
    for w in workers:
        w.join()

    raw_lines.sort(key=lambda line: line[0])
    transcript_lines = [
        (str(t - started).split(".")[0], f"**{speaker}:** {text}" if label else text)
        for t, speaker, text in raw_lines
    ]
    return transcript_lines, started


def summarize_and_save(
    title: str,
    transcript_lines: list[tuple[str, str]],
    started: datetime,
    notes_dir: Path,
    ollama_model: str = summ.DEFAULT_MODEL,
    context: str = "",
    no_summary: bool = False,
) -> Path:
    summary = None
    if not no_summary and transcript_lines:
        text = "\n".join(t for _, t in transcript_lines)
        if not summ.check_model(ollama_model):
            console.print(
                f"[yellow]Ollama model '{ollama_model}' unavailable — "
                "saving transcript without summary.[/yellow]"
            )
        else:
            with console.status(f"Summarizing with {ollama_model} (local)…"):
                try:
                    summary = summ.summarize(text, model=ollama_model, context=context)
                except summ.OllamaError as exc:
                    console.print(f"[red]{exc}[/red]")

    path = notes.save_note(title, transcript_lines, summary, notes_dir, started)
    console.print(f"\n[bold green]Note saved:[/bold green] {path}")
    if summary:
        console.rule("Summary")
        console.print(summary)
    return path
