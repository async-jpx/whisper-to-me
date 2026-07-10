"""runner.watch_loop — prompt (accept/ignore) mode and meeting-end auto-stop.

No audio devices anywhere: the watch module's detection functions are
monkeypatched (on non-macOS they are already a stub, see conftest) and
record_session/summarize_and_save are replaced with fakes.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from whisper_to_me import runner
from whisper_to_me.runner import WatchOptions, watch_loop


def make_opts(tmp_path, confirm=False) -> WatchOptions:
    return WatchOptions(
        title=None,
        device=None,
        system_device="off",
        keep_echoes=False,
        use_aec=False,
        poll=0.01,
        silence_timeout=30.0,
        notes_dir=tmp_path,
        ollama_model="m",
        context="",
        no_summary=True,
        confirm=confirm,
    )


@pytest.fixture()
def quiet_watch(monkeypatch):
    """Baseline watch stubs: no meeting, no title hints, silent notify."""
    monkeypatch.setattr(runner.watch, "detect_meeting", lambda: None)
    monkeypatch.setattr(runner.watch, "meeting_title_hint", lambda trigger: None)
    monkeypatch.setattr(runner.watch, "zoom_meeting_active", lambda: False)
    monkeypatch.setattr(
        runner.watch, "mic_in_use_by_others", lambda exclude_pids=frozenset(): None
    )
    monkeypatch.setattr(runner.watch, "notify", lambda title, message: None)


def run_loop(opts, decision=None, events=None, timeout=5.0):
    """Run watch_loop in a thread; returns (stop_event, join)."""
    stop_event = threading.Event()
    thread = threading.Thread(
        target=watch_loop,
        args=(lambda: object(), opts),
        kwargs={"events": events, "stop_event": stop_event, "decision": decision},
        daemon=True,
    )
    thread.start()

    def join():
        thread.join(timeout=timeout)
        assert not thread.is_alive(), "watch_loop did not stop"

    return stop_event, join


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_prompt_accept_records(quiet_watch, monkeypatch, tmp_path):
    trigger = {"value": "mic"}
    monkeypatch.setattr(runner.watch, "detect_meeting", lambda: trigger["value"])

    recorded = threading.Event()

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        recorded.set()
        trigger["value"] = None  # the meeting ends during the recording
        return [], datetime.now()

    saved = []
    monkeypatch.setattr(runner, "record_session", fake_record_session)
    monkeypatch.setattr(
        runner, "summarize_and_save", lambda *a, **kw: saved.append(a[0])
    )

    answers = ["accept"]
    events = []
    stop_event, join = run_loop(
        make_opts(tmp_path, confirm=True),
        decision=lambda: answers.pop() if answers else None,
        events=events.append,
    )
    assert wait_for(recorded.is_set)
    assert wait_for(lambda: len(saved) == 1)
    stop_event.set()
    join()

    types = [e["type"] for e in events]
    assert "meeting_detected" in types
    prompt_states = [e for e in events if e["type"] == "status" and e["state"] == "prompting"]
    assert prompt_states and prompt_states[0]["title"]


def test_prompt_ignore_skips_recording(quiet_watch, monkeypatch, tmp_path):
    trigger = {"value": "mic"}
    monkeypatch.setattr(runner.watch, "detect_meeting", lambda: trigger["value"])
    monkeypatch.setattr(
        runner, "record_session", lambda *a, **kw: pytest.fail("must not record")
    )

    answered = threading.Event()

    def decision():
        answered.set()
        trigger["value"] = None  # let the ignored meeting end right away
        return "ignore"

    events = []
    stop_event, join = run_loop(
        make_opts(tmp_path, confirm=True), decision=decision, events=events.append
    )
    assert wait_for(answered.is_set)
    # after the ignore the loop goes back to watching
    assert wait_for(
        lambda: [e for e in events if e["type"] == "status"][-1]["state"] == "watching"
    )
    stop_event.set()
    join()


def test_prompt_dismisses_when_meeting_ends_unanswered(quiet_watch, monkeypatch, tmp_path):
    trigger = {"value": "mic"}
    monkeypatch.setattr(runner.watch, "detect_meeting", lambda: trigger["value"])
    monkeypatch.setattr(
        runner, "record_session", lambda *a, **kw: pytest.fail("must not record")
    )

    events = []
    prompted = threading.Event()

    def sink(event):
        events.append(event)
        if event.get("state") == "prompting":
            prompted.set()
            trigger["value"] = None  # meeting ends while the prompt is up

    stop_event, join = run_loop(
        make_opts(tmp_path, confirm=True), decision=lambda: None, events=sink
    )
    assert wait_for(prompted.is_set)
    assert wait_for(
        lambda: [e for e in events if e["type"] == "status"][-1]["state"] == "watching"
    )
    stop_event.set()
    join()


def test_auto_mode_records_without_prompt(quiet_watch, monkeypatch, tmp_path):
    trigger = {"value": "mic"}
    monkeypatch.setattr(runner.watch, "detect_meeting", lambda: trigger["value"])

    recorded = threading.Event()

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        recorded.set()
        trigger["value"] = None
        return [], datetime.now()

    monkeypatch.setattr(runner, "record_session", fake_record_session)
    monkeypatch.setattr(runner, "summarize_and_save", lambda *a, **kw: None)

    events = []
    stop_event, join = run_loop(make_opts(tmp_path, confirm=False), events=events.append)
    assert wait_for(recorded.is_set)
    stop_event.set()
    join()
    assert not any(e["type"] == "meeting_detected" for e in events)
    assert not any(
        e["type"] == "status" and e["state"] == "prompting" for e in events
    )


# ---------- meeting-end auto-stop (the should_stop closure) ----------


class FakeRecorder:
    peak_level = 0.0
    helper_pid = None


def capture_should_stop(quiet_watch, monkeypatch, tmp_path, trigger_name="mic"):
    """Run one watch cycle with a fake record_session that hands the
    should_stop closure back to the test instead of recording."""
    trigger = {"value": trigger_name}
    monkeypatch.setattr(runner.watch, "detect_meeting", lambda: trigger["value"])

    captured = {}
    got = threading.Event()
    release = threading.Event()

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        captured["should_stop"] = kwargs["should_stop"]
        got.set()
        release.wait(5)
        trigger["value"] = None
        return [], datetime.now()

    monkeypatch.setattr(runner, "record_session", fake_record_session)
    monkeypatch.setattr(runner, "summarize_and_save", lambda *a, **kw: None)

    stop_event, join = run_loop(make_opts(tmp_path, confirm=False))
    assert wait_for(got.is_set)

    def finish():
        release.set()
        stop_event.set()
        join()

    return captured["should_stop"], finish


def test_stop_when_call_app_releases_mic(quiet_watch, monkeypatch, tmp_path):
    should_stop, finish = capture_should_stop(quiet_watch, monkeypatch, tmp_path)
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
    finish()


def test_mic_regrab_resets_release_grace(quiet_watch, monkeypatch, tmp_path):
    should_stop, finish = capture_should_stop(quiet_watch, monkeypatch, tmp_path)
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
    finish()


def test_release_signal_needs_call_app_seen_first(quiet_watch, monkeypatch, tmp_path):
    """If nobody else was ever seen on the mic (e.g. detection raced the app),
    a False reading must not end the meeting — only the silence timeout may."""
    should_stop, finish = capture_should_stop(quiet_watch, monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "MIC_RELEASE_GRACE", 0.0)
    monkeypatch.setattr(
        runner.watch, "mic_in_use_by_others", lambda exclude_pids=frozenset(): False
    )
    recs = [FakeRecorder()]
    assert should_stop(recs) is False
    time.sleep(0.02)
    assert should_stop(recs) is False
    finish()


def test_unavailable_api_falls_back_to_silence_timeout(
    quiet_watch, monkeypatch, tmp_path
):
    should_stop, finish = capture_should_stop(quiet_watch, monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner.watch, "mic_in_use_by_others", lambda exclude_pids=frozenset(): None
    )
    recs = [FakeRecorder()]
    assert should_stop(recs) is False  # None = no signal, never a stop
    finish()


def test_zoom_end_still_stops(quiet_watch, monkeypatch, tmp_path):
    should_stop, finish = capture_should_stop(
        quiet_watch, monkeypatch, tmp_path, trigger_name="zoom"
    )
    monkeypatch.setattr(runner.watch, "zoom_meeting_active", lambda: False)
    assert should_stop([FakeRecorder()]) is True
    finish()


def test_helper_pids_are_excluded(quiet_watch, monkeypatch, tmp_path):
    should_stop, finish = capture_should_stop(quiet_watch, monkeypatch, tmp_path)
    seen = {}

    def fake_others(exclude_pids=frozenset()):
        seen["exclude"] = exclude_pids
        return True

    monkeypatch.setattr(runner.watch, "mic_in_use_by_others", fake_others)
    tap = FakeRecorder()
    tap.helper_pid = 4321
    should_stop([FakeRecorder(), tap])
    assert seen["exclude"] == {4321}
    finish()
