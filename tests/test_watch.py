"""watch.py — who counts as "another app on the microphone", mic-free:
the CoreAudio/libproc reads are replaced by a scripted process table."""

from __future__ import annotations

import os

import pytest

from whisper_to_me import watch

pytestmark = pytest.mark.skipif(
    not hasattr(watch, "_other_input_pids"), reason="needs macOS CoreAudio"
)

APPS = {
    101: ("/Applications/zoom.us.app/Contents/MacOS/zoom.us", "Zoom"),
    102: ("/System/Applications/VoiceMemos.app/Contents/MacOS/VoiceMemos", "Voice Memos"),
    103: ("/usr/libexec/replayd", None),
    104: ("/usr/local/bin/sox", None),
    105: ("/tmp/helper/system-audio-tap", None),
}


@pytest.fixture()
def on_mic(monkeypatch):
    """Set which pids run audio input; returns a setter."""
    running: list[int] = []
    monkeypatch.setattr(watch, "_get_object_list", lambda obj, sel: list(running))
    monkeypatch.setattr(watch, "_get_u32_property", lambda obj, sel: 1)
    monkeypatch.setattr(watch, "_get_pid_property", lambda obj, sel: obj)
    monkeypatch.setattr(watch, "_exe_path", lambda pid: APPS.get(pid, ("", None))[0])
    monkeypatch.setattr(watch, "app_name_for_pid", lambda pid: APPS.get(pid, ("", None))[1])
    monkeypatch.setattr(watch, "mic_in_use", lambda: bool(running))
    monkeypatch.setattr(watch, "zoom_meeting_active", lambda: False)

    def set_running(*pids: int) -> None:
        running[:] = pids

    return set_running


def test_screen_capture_daemon_is_not_a_call_app(on_mic):
    on_mic(os.getpid(), 103, 105)
    assert watch.mic_in_use_by_others(frozenset({105})) is False
    on_mic(os.getpid(), 101, 103, 105)
    assert watch._other_input_pids(frozenset({105})) == [101]


def test_only_ignored_apps_on_the_mic_never_prompt(on_mic):
    ignored = frozenset({"Voice Memos"})
    on_mic(102)
    assert watch.detect_meeting() == "mic"
    assert watch.detect_meeting(ignored) is None
    on_mic(102, 101)
    assert watch.detect_meeting(ignored) == "mic"
    assert watch.mic_app_name(ignored=ignored) == "Zoom"


def test_an_unnamed_mic_user_still_prompts(on_mic):
    on_mic(102, 104)
    assert watch.detect_meeting(frozenset({"Voice Memos"})) == "mic"
    assert watch.mic_app_name(ignored=frozenset({"Voice Memos"})) is None


def test_ignoring_zoom_skips_its_meeting_trigger(on_mic, monkeypatch):
    monkeypatch.setattr(watch, "zoom_meeting_active", lambda: True)
    on_mic(101)
    assert watch.detect_meeting() == "zoom"
    assert watch.detect_meeting(frozenset({"Zoom"})) is None


def test_unavailable_process_api_still_prompts(on_mic, monkeypatch):
    on_mic(102)
    monkeypatch.setattr(watch, "_get_object_list", lambda obj, sel: None)
    assert watch.detect_meeting(frozenset({"Voice Memos"})) == "mic"
