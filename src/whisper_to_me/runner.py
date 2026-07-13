"""Reusable meeting-watch loop, shared by `wtm watch` (cli.py) and the
`wtm serve` daemon (server.py) — same detection, title-hint, and
record/summarize cycle either way.

Two ways to react to a detected meeting:
- auto mode (the CLI default): start recording immediately, like before.
- confirm mode (the daemon default): emit a `meeting_detected` event and hold
  in a "prompting" state until the user accepts or ignores — the Notion-style
  popup flow. The prompt dismisses itself if the meeting ends unanswered.

Recordings end on their own when the meeting does: Zoom's helper process
exits, the call app releases the microphone (watch.mic_in_use_by_others,
macOS 14+), or — the fallback that always works — nothing has been heard for
`silence_timeout` seconds.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import audio, briefs, templates, watch
from .session import EventSink, console, record_session, resolve_sink, summarize_and_save

# How long the call app must stay off the microphone before we call the
# meeting over. Short re-grabs (device switches, reconnects) are common, so
# don't trigger on a blip; the user's own speech doesn't matter here — this
# signal is about the *app*, not the audio.
MIC_RELEASE_GRACE = 10.0


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
    template: str | None = None
    diarize: bool = False
    confirm: bool = False  # ask before recording (needs a `decision` source)


def watch_loop(
    get_transcriber: Callable[[], object],
    opts: WatchOptions,
    events: EventSink | None = None,
    stop_event: threading.Event | None = None,
    scratchpad: Callable[[], str] | None = None,
    clear_scratchpad: Callable[[], None] | None = None,
    decision: Callable[[], str | None] | None = None,
) -> None:
    """Poll for a meeting, (optionally ask first,) record it, summarize+save,
    then wait for it to clear before watching again — until Ctrl-C or
    stop_event.is_set().

    `get_transcriber` is called only when a recording actually starts, so a
    daemon that watches from boot doesn't hold the Whisper model for nothing.

    `scratchpad`/`clear_scratchpad` (daemon only) read and reset the
    note-taker's live notes per meeting, so meeting 2 never inherits
    meeting 1's notes; the CLI passes neither and behaves as before.

    `decision` (daemon only, with opts.confirm) is polled while prompting and
    returns "accept"/"ignore" once the user answered, else None."""
    sink = resolve_sink(events)
    prompt_mode = opts.confirm and decision is not None

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def wait(seconds: float) -> None:
        if stop_event is not None:
            stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    def wait_for_clear() -> bool:
        """Wait out the current trigger so we don't instantly re-detect the
        tail of the same meeting; False when a stop was requested."""
        while watch.detect_meeting():
            if stopped():
                return False
            wait(opts.poll)
        return not stopped()

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
            # An explicit --template wins; otherwise auto-suggest from the real
            # meeting name (the calendar/Zoom hint or --title), never from the
            # timestamp placeholder.
            template = opts.template or templates.suggest_template(opts.title or hint)

            if prompt_mode:
                # Notion-style: ask before recording. The prompt stays up
                # while the meeting is live and dismisses itself if the
                # meeting ends unanswered. Whisper preloads in the background
                # so an accepted recording starts without a model-load gap.
                sink({"type": "meeting_detected", "trigger": trigger, "title": title})
                sink({"type": "status", "state": "prompting", "title": title, "started": None})
                watch.notify("whisper-to-me", f"Meeting detected — record '{title}'?")
                threading.Thread(target=get_transcriber, daemon=True).start()
                choice = None
                while choice is None:
                    if stopped():
                        return
                    choice = decision()
                    if choice is None:
                        if watch.detect_meeting() is None:
                            choice = "ignore"  # meeting ended unanswered
                        else:
                            wait(1.0)
                if choice != "accept":
                    # Drop the prompt right away (the popup hides on this
                    # status), then sit out the rest of the ignored meeting so
                    # it isn't instantly re-detected and re-prompted.
                    console.print("[dim]Meeting ignored — watching again.[/dim]")
                    sink({"type": "status", "state": "watching", "title": None, "started": None})
                    if not wait_for_clear():
                        return
                    continue
            else:
                watch.notify("whisper-to-me", f"Meeting detected — taking notes: {title}")

            console.print(
                f"[bold green]● Meeting detected ({trigger})[/bold green] — recording '{title}'\n"
            )

            # Brief: only for a real meeting name (hint or --title), never the
            # timestamp placeholder. The live journal doesn't exist yet, so no
            # note excludes itself here.
            if opts.title or hint:
                brief = briefs.find_brief(opts.notes_dir, title)
                if brief:
                    sink({"type": "brief", **brief})
                    safe = re.sub(r'["\\]', "", brief["title"])
                    watch.notify("whisper-to-me", f"Last time: {safe}")

            transcriber = get_transcriber()
            last_speech = time.monotonic()
            mic_released_at: float | None = None
            call_app_seen = False  # arm mic-release only after the app showed up

            def should_stop(recorders) -> bool:
                nonlocal last_speech, mic_released_at, call_app_seen
                if max(r.peak_level for r in recorders) >= audio.SILENCE_RMS:
                    last_speech = time.monotonic()
                if trigger == "zoom" and not watch.zoom_meeting_active():
                    console.print("[yellow]Zoom meeting ended.[/yellow]")
                    return True
                # macOS 14+: is any process besides us (and our tap helper)
                # still running audio input? When the call app lets go of the
                # mic and stays off it for MIC_RELEASE_GRACE, the meeting is
                # over. None = API unavailable → the silence timeout below
                # stays the only generic end signal.
                helpers = frozenset(
                    pid for r in recorders if (pid := r.helper_pid) is not None
                )
                others = watch.mic_in_use_by_others(helpers)
                if others:
                    call_app_seen = True
                    mic_released_at = None
                elif others is False and call_app_seen:
                    if mic_released_at is None:
                        mic_released_at = time.monotonic()
                    elif time.monotonic() - mic_released_at > MIC_RELEASE_GRACE:
                        console.print(
                            "[yellow]The call app released the microphone — meeting over.[/yellow]"
                        )
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
                diarize=opts.diarize,
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
                user_notes=scratchpad() if scratchpad is not None else "",
                template=template,
                events=sink,
            )
            if clear_scratchpad is not None:
                clear_scratchpad()
            watch.notify("whisper-to-me", f"Notes saved for: {title}")

            if not wait_for_clear():
                return
            console.print("\n[bold cyan]👂 Watching for meetings…[/bold cyan]\n")
            sink({"type": "status", "state": "watching", "title": None, "started": None})
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/dim]")
