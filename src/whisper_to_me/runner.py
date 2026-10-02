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
`silence_timeout` seconds (meeting_end_condition).

The daemon's lifecycle is modelled as data here: one `State` value
(Idle | Prompting | Active) changed only by the pure transition functions
below, which server.py's SessionManager applies under its lock.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from . import audio, briefs, templates, watch
from .session import (
    EventSink,
    StopCondition,
    console,
    record_session,
    resolve_sink,
    summarize_and_save,
)

# How long the call app must stay off the microphone before we call the
# meeting over. Short re-grabs (device switches, reconnects) are common, so
# don't trigger on a blip; the user's own speech doesn't matter here — this
# signal is about the *app*, not the audio.
MIC_RELEASE_GRACE = 10.0

# An unanswered prompt counts as dismissed after this long.
PROMPT_TIMEOUT_S = 60.0

Trigger = Literal["zoom", "mic"]
Origin = Literal["manual", "detected", "simulate"]
Phase = Literal["starting", "recording", "stopping", "summarizing"]
Answer = Literal["record", "dismiss"]


# -- the daemon's meeting lifecycle: one State value, pure transitions -------
#
#   Idle ──trigger──▶ Ask ──(title hint)──▶ open_prompt ──▶ Prompting(p)
#   Prompting(p) ──answer(p.id, record)──▶ Active(detected)
#   Prompting(p) ──answer(p.id, dismiss) / deadline──▶ Idle(sitting_out=True)
#   Prompting(p) ──trigger gone──▶ Idle()               (ended unanswered)
#   Idle | Prompting ──start(manual / simulate)──▶ Active  (prompt superseded)
#   Active ──session thread exits──▶ Idle(sitting_out=True)
#   Idle(sitting_out=True) ──trigger gone──▶ Idle()


@dataclass(frozen=True)
class Prompt:
    """A detected meeting awaiting Record / Dismiss."""

    id: str  # random per prompt: a stale or foreign answer can never match
    trigger: Trigger
    title: str
    real_title: bool  # title came from a calendar/Zoom hint, not a placeholder
    template: str | None
    deadline: float  # time.monotonic(); at or past it the prompt is dismissed


@dataclass(frozen=True)
class SimulateSource:
    mic: str
    system: str | None
    no_summary: bool


@dataclass(frozen=True)
class SessionPlan:
    """Everything a session thread needs, decided before it starts — one plan
    type for every origin so the daemon has a single run path."""

    origin: Origin
    title: str
    template: str | None
    auto_title: bool  # let the summarizer replace a placeholder title
    find_brief: bool  # "Last time…" lookup, only for a real meeting name
    trigger: Trigger | None = None  # detected only: arms meeting_end_condition
    simulate: SimulateSource | None = None

    @property
    def stoppable(self) -> bool:
        # simulate_session takes no stop_event: a FileRecorder runs to EOF.
        return self.origin != "simulate"


@dataclass(frozen=True)
class Idle:
    # A meeting we already handled (recorded, dismissed, timed out) is still
    # detected: don't prompt for it again until its trigger goes away.
    sitting_out: bool = False


@dataclass(frozen=True)
class Prompting:
    prompt: Prompt


@dataclass(frozen=True)
class Active:
    plan: SessionPlan
    phase: Phase
    started: datetime
    stop: threading.Event


State = Idle | Prompting | Active


@dataclass(frozen=True)
class Ask:
    """observe's verdict: a new meeting appeared. Fetch its title hint, then
    call open_prompt."""

    trigger: Trigger


@dataclass(frozen=True)
class Refused:
    """The request doesn't apply to the current state (HTTP 409)."""

    reason: str


def observe(state: State, trigger: Trigger | None, now: float) -> State | Ask:
    """One detector poll; `now` is time.monotonic(). Returns `state` itself
    when nothing changes."""
    match state:
        case Active():
            return state
        case Idle(sitting_out=True):
            return Idle() if trigger is None else state
        case Idle():
            return state if trigger is None else Ask(trigger)
        case Prompting(prompt=prompt):
            if trigger is None:
                return Idle()
            if now >= prompt.deadline:
                return Idle(sitting_out=True)
            return state


def open_prompt(
    state: State,
    prompt_id: str,
    trigger: Trigger,
    hint: str | None,
    default_template: str | None,
    now: float,
    wall_now: datetime,
    timeout_s: float = PROMPT_TIMEOUT_S,
) -> State:
    """Compare-and-set after the title-hint I/O: only a plain Idle becomes
    Prompting. Anything that happened meanwhile (a manual record) wins."""
    if state != Idle():
        return state
    title = hint or (
        f"{'Zoom meeting' if trigger == 'zoom' else 'Meeting'} {wall_now:%d %b %H:%M}"
    )
    # An explicit template wins; otherwise auto-suggest from the real meeting
    # name, never from the timestamp placeholder.
    template = default_template or templates.suggest_template(hint)
    return Prompting(
        Prompt(
            id=prompt_id,
            trigger=trigger,
            title=title,
            real_title=hint is not None,
            template=template,
            deadline=now + timeout_s,
        )
    )


def answer(
    state: State, prompt_id: str, choice: Answer, now: datetime, now_mono: float
) -> State | Refused:
    """Only the live prompt, before its deadline, can be answered. A late
    click, a second surface answering, or an answer to a prompt that a
    manual record superseded is refused — never carried over."""
    match state:
        case Prompting(prompt=prompt) if prompt.id == prompt_id and now_mono < prompt.deadline:
            if choice == "dismiss":
                return Idle(sitting_out=True)
            return Active(detected_plan(prompt), "starting", now, threading.Event())
    return Refused("prompt expired")


def start(state: State, plan: SessionPlan, now: datetime) -> State | Refused:
    """Manual record / simulate: allowed from Idle or Prompting (the prompt
    is superseded), refused while a session is active."""
    if isinstance(state, Active):
        return Refused(f"busy: {state.phase}")
    return Active(plan, "starting", now, threading.Event())


def stop(state: State) -> State | Refused:
    """Any stoppable origin — a detected recording stops exactly like a
    manual one. Repeat stops while winding down are no-ops. The caller sets
    the returned state's `stop` event."""
    match state:
        case Active(plan=plan) if not plan.stoppable:
            return Refused(f"busy: {state.phase}")
        case Active(phase="starting" | "recording"):
            return replace(state, phase="stopping")
        case Active():
            return state
        case Idle():
            return Refused("busy: idle")
        case Prompting():
            return Refused("busy: prompting")


def recording_started(state: State, started: datetime) -> State:
    """record_session's own status event: audio is flowing. Takes its
    `started` (simulate's epoch names the live journal) but never resurrects
    a phase past "recording" — the wind-down is what the user is watching."""
    if not isinstance(state, Active):
        return state
    phase = "recording" if state.phase == "starting" else state.phase
    return replace(state, phase=phase, started=started)


def summarizing(state: State) -> State:
    if not isinstance(state, Active):
        return state
    return replace(state, phase="summarizing")


def session_ended(state: State) -> State:
    """Whatever ended the session, sit out a meeting that is still live: Stop
    must never be followed by an instant re-prompt for the same call."""
    return Idle(sitting_out=True)


def manual_plan(title: str | None, template: str | None, now: datetime) -> SessionPlan:
    # Brief only for a user-supplied title: the placeholder can't match a
    # prior meeting meaningfully.
    return SessionPlan(
        origin="manual",
        title=title or f"Meeting {now:%d %b %H:%M}",
        template=template,
        auto_title=title is None,
        find_brief=bool(title),
    )


def detected_plan(prompt: Prompt) -> SessionPlan:
    return SessionPlan(
        origin="detected",
        title=prompt.title,
        template=prompt.template,
        auto_title=not prompt.real_title,
        find_brief=prompt.real_title,
        trigger=prompt.trigger,
    )


def simulate_plan(src: SimulateSource, template: str | None, now: datetime) -> SessionPlan:
    # "Simulation <time>" is real-ish, so a second run exercises the brief
    # path mic-free by matching the first run's note.
    return SessionPlan(
        origin="simulate",
        title=f"Simulation {now:%d %b %H:%M}",
        template=template,
        auto_title=True,
        find_brief=True,
        simulate=src,
    )


def meeting_end_condition(trigger: Trigger, silence_timeout: float) -> StopCondition:
    """When a detected recording ends on its own: Zoom's helper exits, the
    call app releases the microphone, or nothing is heard for
    `silence_timeout` seconds."""
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
        if time.monotonic() - last_speech > silence_timeout:
            console.print(
                f"[yellow]No audio for {silence_timeout:.0f}s — meeting seems over.[/yellow]"
            )
            return True
        return False

    return should_stop


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
            should_stop = meeting_end_condition(trigger, opts.silence_timeout)

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
