"""runner's meeting lifecycle: the pure State transitions and the
meeting-end auto-stop condition."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from whisper_to_me import runner
from whisper_to_me.runner import (
    Active,
    Ask,
    Idle,
    Prompting,
    Refused,
    SimulateSource,
)

NOW = datetime(2026, 10, 2, 14, 3)


def prompting(prompt_id="a1", trigger="zoom", hint="Weekly sync", deadline=100.0):
    state = runner.open_prompt(
        Idle(), prompt_id, trigger, hint, None, now=deadline - 60.0, wall_now=NOW
    )
    assert isinstance(state, Prompting)
    return state


def manual_active():
    return runner.start(Idle(), runner.manual_plan("Standup", None, NOW), NOW)


def test_idle_with_trigger_asks():
    assert runner.observe(Idle(), "zoom", 0.0) == Ask("zoom")
    assert runner.observe(Idle(), None, 0.0) == Idle()


def test_sitting_out_holds_until_trigger_clears():
    sitting = Idle(sitting_out=True)
    assert runner.observe(sitting, "mic", 0.0) is sitting
    assert runner.observe(sitting, None, 0.0) == Idle()


def test_prompt_ends_unanswered_when_meeting_goes_away():
    assert runner.observe(prompting(), None, 50.0) == Idle(sitting_out=False)


def test_prompt_times_out_into_sitting_out():
    state = prompting(deadline=100.0)
    assert runner.observe(state, "zoom", 99.9) is state
    assert runner.observe(state, "zoom", 100.0) == Idle(sitting_out=True)


def test_timeout_then_no_reprompt_for_the_same_meeting():
    state = runner.observe(prompting(deadline=100.0), "zoom", 100.0)
    for t in (103.0, 106.0, 109.0):
        state = runner.observe(state, "zoom", t)
        assert state == Idle(sitting_out=True)
    assert runner.observe(runner.observe(state, None, 112.0), "zoom", 115.0) == Ask("zoom")


def test_active_ignores_detection():
    active = manual_active()
    assert runner.observe(active, "mic", 0.0) is active
    assert runner.observe(active, None, 0.0) is active


def test_open_prompt_loses_to_a_session_started_during_the_hint():
    active = manual_active()
    assert runner.open_prompt(active, "a1", "zoom", None, None, 0.0, NOW) is active
    sitting = Idle(sitting_out=True)
    assert runner.open_prompt(sitting, "a1", "zoom", None, None, 0.0, NOW) is sitting


def test_open_prompt_title_and_template_rules():
    p = prompting(hint="Weekly standup").prompt
    assert (p.title, p.real_title, p.template) == ("Weekly standup", True, "standup")
    assert p.deadline == 100.0

    zoom = prompting(trigger="zoom", hint=None).prompt
    assert (zoom.title, zoom.real_title, zoom.template) == (
        "Zoom meeting 02 Oct 14:03", False, None,
    )
    mic = prompting(trigger="mic", hint=None).prompt
    assert mic.title == "Meeting 02 Oct 14:03"

    forced = runner.open_prompt(Idle(), "a1", "mic", "Weekly standup", "interview", 0.0, NOW)
    assert forced.prompt.template == "interview"


def test_open_prompt_uses_the_timeout():
    state = runner.open_prompt(Idle(), "a1", "mic", None, None, 10.0, NOW, timeout_s=0.5)
    assert state.prompt.deadline == 10.5


def test_record_answer_starts_a_detected_session():
    state = runner.answer(prompting(hint="Weekly sync"), "a1", "record", NOW, 50.0)
    assert isinstance(state, Active)
    assert state.phase == "starting" and state.started == NOW
    assert state.plan.origin == "detected" and state.plan.trigger == "zoom"
    assert state.plan.title == "Weekly sync"
    assert (state.plan.auto_title, state.plan.find_brief) == (False, True)
    assert not state.stop.is_set()


def test_record_answer_on_placeholder_title_infers_title():
    state = runner.answer(prompting(hint=None), "a1", "record", NOW, 50.0)
    assert (state.plan.auto_title, state.plan.find_brief) == (True, False)


def test_dismiss_answer_sits_out():
    assert runner.answer(prompting(), "a1", "dismiss", NOW, 50.0) == Idle(sitting_out=True)


def test_answer_after_deadline_refused():
    state = prompting(deadline=100.0)
    assert runner.answer(state, "a1", "record", NOW, 100.0) == Refused("prompt expired")


@pytest.mark.parametrize("state", [Idle(), Idle(sitting_out=True), manual_active()])
def test_answer_without_a_live_prompt_refused(state):
    assert isinstance(runner.answer(state, "a1", "record", NOW, 0.0), Refused)


def test_stale_answer_never_reaches_the_next_prompt():
    state = runner.observe(prompting("a1", deadline=100.0), "zoom", 100.0)
    state = runner.observe(state, None, 103.0)
    assert runner.observe(state, "zoom", 106.0) == Ask("zoom")
    second = runner.open_prompt(state, "b2", "zoom", None, None, 106.0, NOW)
    assert runner.answer(second, "a1", "record", NOW, 107.0) == Refused("prompt expired")
    assert runner.answer(second, "b2", "record", NOW, 107.0).plan.origin == "detected"


def test_manual_start_supersedes_a_prompt():
    state = runner.start(prompting(), runner.manual_plan(None, None, NOW), NOW)
    assert isinstance(state, Active) and state.plan.origin == "manual"
    assert state.plan.title == "Meeting 02 Oct 14:03"
    assert (state.plan.auto_title, state.plan.find_brief) == (True, False)


def test_start_while_active_refused():
    assert runner.start(manual_active(), runner.manual_plan(None, None, NOW), NOW) == Refused(
        "busy: starting"
    )


def test_simulate_plan():
    src = SimulateSource("a.wav", None, True)
    plan = runner.simulate_plan(src, "standup", NOW)
    assert plan.title == "Simulation 02 Oct 14:03" and plan.simulate is src
    assert (plan.auto_title, plan.find_brief, plan.stoppable) == (True, True, False)


def test_stop_detected_recording_then_idempotent():
    active = runner.answer(prompting(), "a1", "record", NOW, 50.0)
    stopping = runner.stop(active)
    assert stopping.phase == "stopping" and stopping.stop is active.stop
    assert runner.stop(stopping) is stopping
    summarizing = runner.summarizing(stopping)
    assert runner.stop(summarizing) is summarizing


def test_stop_refuses_simulate_and_no_session():
    sim = runner.start(
        Idle(), runner.simulate_plan(SimulateSource("a.wav", None, True), None, NOW), NOW
    )
    assert runner.stop(sim) == Refused("busy: starting")
    assert runner.stop(Idle()) == Refused("busy: idle")
    assert runner.stop(prompting()) == Refused("busy: prompting")


def test_recording_started_never_resurrects_stopping():
    later = datetime(2026, 10, 2, 14, 4)
    recording = runner.recording_started(manual_active(), later)
    assert (recording.phase, recording.started) == ("recording", later)
    stopping = runner.stop(manual_active())
    assert runner.recording_started(stopping, later).phase == "stopping"


def test_session_end_sits_out():
    assert runner.session_ended(manual_active()) == Idle(sitting_out=True)


class FakeRecorder:
    peak_level = 0.0
    helper_pid = None


@pytest.fixture()
def quiet_watch(monkeypatch):
    monkeypatch.setattr(runner.watch, "zoom_meeting_active", lambda: False)
    monkeypatch.setattr(
        runner.watch, "mic_in_use_by_others", lambda exclude_pids=frozenset(): None
    )


def test_stop_when_call_app_releases_mic(quiet_watch, monkeypatch):
    should_stop = runner.meeting_end_condition("mic", 30.0)
    monkeypatch.setattr(runner, "MIC_RELEASE_GRACE", 0.05)

    on_mic = {"value": True}
    monkeypatch.setattr(
        runner.watch,
        "mic_in_use_by_others",
        lambda exclude_pids=frozenset(): on_mic["value"],
    )
    recs = [FakeRecorder()]
    assert should_stop(recs) is False  # the call app is on the mic

    on_mic["value"] = False  # …and lets go
    assert should_stop(recs) is False  # grace period starts
    time.sleep(0.08)
    assert should_stop(recs) is True  # released past the grace: meeting over


def test_mic_regrab_resets_release_grace(quiet_watch, monkeypatch):
    should_stop = runner.meeting_end_condition("mic", 30.0)
    monkeypatch.setattr(runner, "MIC_RELEASE_GRACE", 0.05)

    on_mic = {"value": True}
    monkeypatch.setattr(
        runner.watch,
        "mic_in_use_by_others",
        lambda exclude_pids=frozenset(): on_mic["value"],
    )
    recs = [FakeRecorder()]
    should_stop(recs)
    on_mic["value"] = False
    should_stop(recs)  # release timer starts
    time.sleep(0.08)
    on_mic["value"] = True  # the app re-grabbed the mic (reconnect blip)
    assert should_stop(recs) is False
    on_mic["value"] = False
    assert should_stop(recs) is False  # timer restarted, not expired


def test_release_signal_needs_call_app_seen_first(quiet_watch, monkeypatch):
    """If nobody else was ever seen on the mic (e.g. detection raced the app),
    a False reading must not end the meeting — only the silence timeout may."""
    should_stop = runner.meeting_end_condition("mic", 30.0)
    monkeypatch.setattr(runner, "MIC_RELEASE_GRACE", 0.0)
    monkeypatch.setattr(
        runner.watch, "mic_in_use_by_others", lambda exclude_pids=frozenset(): False
    )
    recs = [FakeRecorder()]
    assert should_stop(recs) is False
    time.sleep(0.02)
    assert should_stop(recs) is False


def test_unavailable_api_falls_back_to_silence_timeout(quiet_watch, monkeypatch):
    should_stop = runner.meeting_end_condition("mic", 0.05)
    monkeypatch.setattr(
        runner.watch, "mic_in_use_by_others", lambda exclude_pids=frozenset(): None
    )
    recs = [FakeRecorder()]
    assert should_stop(recs) is False  # None = no signal, never a stop
    time.sleep(0.08)
    assert should_stop(recs) is True


def test_zoom_end_still_stops(quiet_watch, monkeypatch):
    should_stop = runner.meeting_end_condition("zoom", 30.0)
    monkeypatch.setattr(runner.watch, "zoom_meeting_active", lambda: False)
    assert should_stop([FakeRecorder()]) is True


def test_helper_pids_are_excluded(quiet_watch, monkeypatch):
    should_stop = runner.meeting_end_condition("mic", 30.0)
    seen = {}

    def fake_others(exclude_pids=frozenset()):
        seen["exclude"] = exclude_pids
        return True

    monkeypatch.setattr(runner.watch, "mic_in_use_by_others", fake_others)
    tap = FakeRecorder()
    tap.helper_pid = 4321
    should_stop([FakeRecorder(), tap])
    assert seen["exclude"] == {4321}
