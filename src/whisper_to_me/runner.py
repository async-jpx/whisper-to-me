"""Reusable meeting-watch loop, shared by `wtm watch` (cli.py) and the
`wtm serve` daemon (server.py) — same detection, title-hint, and
record/summarize cycle either way."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import audio, watch
from .session import EventSink, console, record_session, resolve_sink, summarize_and_save


@dataclass
class WatchOptions:
    title: str | None
    device: int | None
    system_device: str
    keep_echoes: bool
    use_aec: bool
    poll: float
    silence_timeout: float
    notes_dir: Path
    ollama_model: str
    context: str
    no_summary: bool


def watch_loop(
    transcriber,
    opts: WatchOptions,
    events: EventSink | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Poll for a meeting, record it, summarize+save, then wait for it to
    clear before watching again — until Ctrl-C or stop_event.is_set()."""
    sink = resolve_sink(events)

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def wait(seconds: float) -> None:
        if stop_event is not None:
            stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    try:
        sink({"type": "status", "state": "watching", "title": None, "started": None})
        while True:
            trigger = watch.detect_meeting()
            if trigger is None:
                if stopped():
                    return
                wait(opts.poll)
                continue

            # Title priority: --title > calendar event / Zoom window topic >
            # placeholder that the summarizer replaces with an inferred title.
            hint = watch.meeting_title_hint(trigger) if opts.title is None else None
            title = opts.title or hint or (
                f"{'Zoom meeting' if trigger == 'zoom' else 'Meeting'} "
                f"{datetime.now():%d %b %H:%M}"
            )
            watch.notify("whisper-to-me", f"Meeting detected — taking notes: {title}")
            console.print(
                f"[bold green]● Meeting detected ({trigger})[/bold green] — recording '{title}'\n"
            )

            last_speech = time.monotonic()

            def should_stop(recorders) -> bool:
                nonlocal last_speech
                if max(r.peak_level for r in recorders) >= audio.SILENCE_RMS:
                    last_speech = time.monotonic()
                if trigger == "zoom" and not watch.zoom_meeting_active():
                    console.print("[yellow]Zoom meeting ended.[/yellow]")
                    return True
                if time.monotonic() - last_speech > opts.silence_timeout:
                    console.print(
                        f"[yellow]No audio for {opts.silence_timeout:.0f}s — meeting seems over.[/yellow]"
                    )
                    return True
                return False

            transcript_lines, started = record_session(
                transcriber,
                title,
                opts.notes_dir,
                device=opts.device,
                system_device=opts.system_device,
                should_stop=should_stop,
                keep_echoes=opts.keep_echoes,
                use_aec=opts.use_aec,
                events=sink,
                stop_event=stop_event,
            )
            # A real name from the calendar/Zoom wins; only infer when we
            # fell back to the timestamp placeholder.
            summarize_and_save(
                title,
                transcript_lines,
                started,
                opts.notes_dir,
                ollama_model=opts.ollama_model,
                context=opts.context,
                no_summary=opts.no_summary,
                auto_title=opts.title is None and hint is None,
                events=sink,
            )
            watch.notify("whisper-to-me", f"Notes saved for: {title}")

            # Wait until the trigger clears so we don't instantly re-record
            # the tail of the same meeting.
            while watch.detect_meeting():
                if stopped():
                    return
                wait(opts.poll)
            console.print("\n[bold cyan]👂 Watching for meetings…[/bold cyan]\n")
            sink({"type": "status", "state": "watching", "title": None, "started": None})
            if stopped():
                return
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/dim]")
