"""The daemon's meeting lifecycle, modelled as data."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from . import audio, templates, watch
from .session import StopCondition, console

# How long the call app must stay off the microphone before we call the
# meeting over. Short re-grabs (device switches, reconnects) are common, so
# don't trigger on a blip; the user's own speech doesn't matter here — this
# signal is about the *app*, not the audio.
MIC_RELEASE_GRACE = 10.0

PROMPT_TIMEOUT_S = 60.0

Trigger = Literal["zoom", "mic"]
Origin = Literal["manual", "detected", "simulate"]
Phase = Literal["starting", "recording", "stopping", "summarizing"]
Answer = Literal["record", "dismiss"]


@dataclass(frozen=True)
class Prompt:
    id: str
    trigger: Trigger
    title: str
    real_title: bool
    template: str | None
    deadline: float
    app: str | None = None


@dataclass(frozen=True)
class SimulateSource:
    mic: str
    system: str | None
    no_summary: bool


@dataclass(frozen=True)
class SessionPlan:
    origin: Origin
    title: str
    template: str | None
    auto_title: bool
    find_brief: bool
    trigger: Trigger | None = None
    simulate: SimulateSource | None = None
    app: str | None = None  # meeting app display name, for the note's frontmatter

    @property
    def stoppable(self) -> bool:
        return self.origin != "simulate"


@dataclass(frozen=True)
class Idle:
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
    trigger: Trigger


@dataclass(frozen=True)
class Refused:
    reason: str


def observe(state: State, trigger: Trigger | None, now: float) -> State | Ask:
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
    app: str | None = None,
) -> State:
    if state != Idle():
        return state
    title = hint or (
        f"{'Zoom meeting' if trigger == 'zoom' else 'Meeting'} {wall_now:%d %b %H:%M}"
    )
    template = templates.resolve_template(default_template, hint)
    return Prompting(
        Prompt(
            id=prompt_id,
            trigger=trigger,
            title=title,
            real_title=hint is not None,
            template=template,
            deadline=now + timeout_s,
            app=app,
        )
    )


def answer(
    state: State, prompt_id: str, choice: Answer, now: datetime, now_mono: float
) -> State | Refused:
    match state:
        case Prompting(prompt=prompt) if prompt.id == prompt_id and now_mono < prompt.deadline:
            if choice == "dismiss":
                return Idle(sitting_out=True)
            return Active(detected_plan(prompt), "starting", now, threading.Event())
    return Refused("prompt expired")


def start(state: State, plan: SessionPlan, now: datetime) -> State | Refused:
    if isinstance(state, Active):
        return Refused(f"busy: {state.phase}")
    return Active(plan, "starting", now, threading.Event())


def stop(state: State) -> State | Refused:
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
    if not isinstance(state, Active):
        return state
    phase = "recording" if state.phase == "starting" else state.phase
    return replace(state, phase=phase, started=started)


def summarizing(state: State) -> State:
    if not isinstance(state, Active):
        return state
    return replace(state, phase="summarizing")


def session_ended(state: State) -> State:
    return Idle(sitting_out=True)


def manual_plan(
    title: str | None, template: str | None, now: datetime, app: str | None = None
) -> SessionPlan:
    return SessionPlan(
        origin="manual",
        title=title or f"Meeting {now:%d %b %H:%M}",
        template=template,
        auto_title=title is None,
        find_brief=bool(title),
        app=app,
    )


def detected_plan(prompt: Prompt) -> SessionPlan:
    return SessionPlan(
        origin="detected",
        title=prompt.title,
        template=prompt.template,
        auto_title=not prompt.real_title,
        find_brief=prompt.real_title,
        trigger=prompt.trigger,
        app=prompt.app,
    )


def simulate_plan(src: SimulateSource, template: str | None, now: datetime) -> SessionPlan:
    return SessionPlan(
        origin="simulate",
        title=f"Simulation {now:%d %b %H:%M}",
        template=template,
        auto_title=True,
        find_brief=True,
        simulate=src,
    )


def meeting_end_condition(trigger: Trigger, silence_timeout: float) -> StopCondition:
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

