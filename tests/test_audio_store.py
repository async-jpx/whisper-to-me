"""Kept meeting audio: timeline placement, mixing, and following the note."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import wave
from datetime import datetime, timedelta

import numpy as np
import pytest

from whisper_to_me import audio_store, notes, session
from whisper_to_me.audio import SAMPLE_RATE

STARTED = datetime(2026, 10, 4, 9, 30)
BLOCK = 1600  # 0.1 s


def _tone(level: float, n: int = BLOCK) -> np.ndarray:
    return np.full(n, level, dtype=np.float32)


def _read_wav(path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32767


def _feed(tap, start_s: float, blocks: int, level: float) -> None:
    for i in range(blocks):
        tap(STARTED + timedelta(seconds=start_s + i * 0.1), _tone(level))


def _mix_tracks(tmp_path, capture) -> np.ndarray:
    for track in capture._tracks:
        track.close()
    total = max(t.samples for t in capture._tracks)
    wav = tmp_path / "mix.wav"
    audio_store._mix(capture._tracks, total, wav)
    return _read_wav(wav)


def test_late_and_dying_source_lands_on_the_shared_timeline(tmp_path):
    capture = audio_store.AudioCapture(tmp_path / "m.md", STARTED)
    mic, system = capture.track(), capture.track()
    _feed(mic, 0.0, 50, 0.1)       # mic: 0 – 5 s
    _feed(system, 2.0, 10, 0.3)    # tap starts late at 2 s, dies at 3 s
    mixed = _mix_tracks(tmp_path, capture)
    capture.discard()

    assert len(mixed) == 5 * SAMPLE_RATE
    at = lambda s: mixed[int(s * SAMPLE_RATE)]  # noqa: E731
    assert at(1.0) == pytest.approx(0.1, abs=1e-3)
    assert at(2.5) == pytest.approx(0.4, abs=1e-3)  # both sources, mixed
    assert at(4.0) == pytest.approx(0.1, abs=1e-3)  # tap died: mic only


def test_stalled_source_resyncs_instead_of_drifting_early(tmp_path):
    capture = audio_store.AudioCapture(tmp_path / "m.md", STARTED)
    tap = capture.track()
    _feed(tap, 0.0, 10, 0.2)   # 0 – 1 s
    _feed(tap, 3.0, 10, 0.2)   # helper stalled 2 s: resumes at 3 s
    mixed = _mix_tracks(tmp_path, capture)
    capture.discard()

    assert mixed[int(2.0 * SAMPLE_RATE)] == 0.0  # the stall is silence
    assert mixed[int(3.5 * SAMPLE_RATE)] == pytest.approx(0.2, abs=1e-3)
    assert len(mixed) == 4 * SAMPLE_RATE


def test_jittery_arrival_stays_contiguous(tmp_path):
    capture = audio_store.AudioCapture(tmp_path / "m.md", STARTED)
    tap = capture.track()
    # A burst of queued blocks all stamped with nearly the same arrival time.
    for i in range(10):
        tap(STARTED + timedelta(seconds=1.0 + i * 0.001), _tone(0.2))
    mixed = _mix_tracks(tmp_path, capture)
    capture.discard()
    assert len(mixed) == 2 * SAMPLE_RATE
    assert np.all(mixed[SAMPLE_RATE:] > 0.19)


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert")
def test_finish_writes_m4a_and_peaks_and_removes_temp(tmp_path):
    note = tmp_path / "meeting.md"
    capture = audio_store.AudioCapture(note, STARTED)
    tap = capture.track()
    _feed(tap, 0.0, 20, 0.0)
    _feed(tap, 2.0, 20, 0.5)
    assert capture.finish() == audio_store.audio_path(note)

    assert audio_store.has_audio(note)
    peaks = json.loads(audio_store.peaks_path(note).read_text())
    assert peaks["duration_s"] == 4.0
    assert peaks["peaks"] == [0.0] * 4 + [1.0] * 4  # one per 0.5 s, normalized
    assert {p.name for p in audio_store.audio_dir(note).iterdir()} == {
        "meeting.m4a", "meeting.peaks.json"
    }


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert")
def test_two_sources_also_keep_the_mic_alone(tmp_path):
    note = tmp_path / "meeting.md"
    capture = audio_store.AudioCapture(note, STARTED)
    mic, system = capture.track(is_mic=True), capture.track()
    _feed(mic, 0.0, 20, 0.2)       # you: 0 – 2 s
    _feed(system, 1.0, 10, 0.4)    # others talk over you: 1 – 2 s
    capture.finish()

    decoded = tmp_path / "mic.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16",
                    str(audio_store.mic_path(note)), str(decoded)], check=True)
    alone = _read_wav(decoded)
    rms = lambda a, b: float(np.sqrt(np.mean(alone[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)] ** 2)))  # noqa: E731
    assert rms(0.2, 0.8) == pytest.approx(0.2, abs=0.03)
    assert rms(1.2, 1.8) == pytest.approx(0.2, abs=0.03)  # mixed would be 0.6
    assert {p.name for p in audio_store.audio_dir(note).iterdir()} == {
        "meeting.m4a", "meeting.mic.m4a", "meeting.peaks.json"
    }


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert")
def test_single_source_keeps_no_mic_copy(tmp_path):
    note = tmp_path / "meeting.md"
    capture = audio_store.AudioCapture(note, STARTED)
    _feed(capture.track(is_mic=True), 0.0, 10, 0.2)
    capture.finish()
    assert audio_store.has_audio(note)
    assert not audio_store.mic_path(note).exists()


def test_peaks_are_capped_for_long_recordings(tmp_path):
    capture = audio_store.AudioCapture(tmp_path / "m.md", STARTED)
    tap = capture.track()
    # 30 minutes at 0.5 s per peak would be 3600 values.
    tap(STARTED + timedelta(minutes=30), _tone(0.1))
    for track in capture._tracks:
        track.close()
    total = capture._tracks[0].samples
    peaks = audio_store._mix(capture._tracks, total, tmp_path / "x.wav")
    capture.discard()
    assert len(peaks) <= audio_store.MAX_PEAKS


def test_disk_error_ends_the_track_not_the_chunker(tmp_path):
    note = tmp_path / "m.md"
    capture = audio_store.AudioCapture(note, STARTED)
    tap = capture.track()

    class FullDisk:
        def seek(self, pos):
            pass

        def write(self, data):
            raise OSError(28, "No space left on device")

        def close(self):
            pass

    capture._tracks[0]._fh = FullDisk()
    _feed(tap, 0.0, 3, 0.2)  # must not raise on the chunker thread
    with pytest.raises(OSError):
        capture.finish()
    assert not audio_store.has_audio(note)
    assert list(audio_store.audio_dir(note).iterdir()) == []


def test_finish_with_no_audio_keeps_nothing(tmp_path):
    note = tmp_path / "m.md"
    capture = audio_store.AudioCapture(note, STARTED)
    capture.track()
    assert capture.finish() is None
    assert list(audio_store.audio_dir(note).iterdir()) == []


def _with_audio(note):
    note.write_text("# n\n", encoding="utf-8")
    audio_store.audio_dir(note).mkdir(parents=True, exist_ok=True)
    audio_store.audio_path(note).write_bytes(b"m4a")
    audio_store.mic_path(note).write_bytes(b"mic")
    audio_store.peaks_path(note).write_text("{}", encoding="utf-8")
    return note


def test_archive_and_restore_carry_the_audio_even_on_a_name_clash(tmp_path):
    note = _with_audio(tmp_path / "standup.md")
    archive = notes.archive_dir(tmp_path)
    archive.mkdir()
    (archive / "standup.md").write_text("older", encoding="utf-8")

    archived = notes.move_note(note, archive)
    assert archived.name == "standup-1.md"
    assert audio_store.audio_path(archived).read_bytes() == b"m4a"
    assert audio_store.peaks_path(archived).is_file()
    assert audio_store.mic_path(archived).read_bytes() == b"mic"
    assert not audio_store.has_audio(note)
    assert not audio_store.has_audio(archive / "standup.md")

    restored = notes.move_note(archived, tmp_path)
    assert audio_store.has_audio(restored)
    assert not audio_store.has_audio(archived)


def test_delete_note_deletes_its_audio(tmp_path):
    note = _with_audio(tmp_path / "x.md")
    notes.delete_note(note)
    assert not note.exists()
    assert not audio_store.audio_path(note).exists()
    assert not audio_store.peaks_path(note).exists()
    assert not audio_store.mic_path(note).exists()


def test_inferred_title_move_takes_the_audio_and_writes_the_app(tmp_path, monkeypatch):
    live = notes.start_live_note("Meeting 04 Oct 09:30", STARTED, tmp_path)
    _with_audio(live)
    live.write_text(live.read_text() + "**[0:00:01]** hi\n", encoding="utf-8")
    monkeypatch.setattr(session.summ, "check_model", lambda model: True)
    monkeypatch.setattr(
        session.summ,
        "summarize_meeting",
        lambda text, **kw: ("## TL;DR\n\nhi", "Budget Review", {"attendees": []}),
    )

    path = session.summarize_and_save(
        "Meeting 04 Oct 09:30", [("0:00:01", "hi")], STARTED, tmp_path,
        auto_title=True, events=lambda e: None, app="Zoom",
    )

    assert path.name == "2026-10-04-0930-budget-review.md"
    assert not live.exists()
    assert audio_store.audio_path(path).read_bytes() == b"m4a"
    assert audio_store.peaks_path(path).is_file()
    assert audio_store.mic_path(path).read_bytes() == b"mic"
    assert not audio_store.audio_path(live).exists()
    assert notes.frontmatter_fields(path.read_text())["app"] == "Zoom"


def test_note_without_app_has_no_app_key(tmp_path):
    path = notes.save_note("T", [], None, tmp_path, STARTED)
    assert "app" not in notes.frontmatter_fields(path.read_text())


def test_purge_drops_unplayed_recordings_but_keeps_notes(tmp_path):
    archive = notes.archive_dir(tmp_path)
    archive.mkdir()
    stale = _with_audio(tmp_path / "stale.md")
    fresh = _with_audio(tmp_path / "fresh.md")
    stale_archived = _with_audio(archive / "old.md")
    now = time.time()
    old = now - 31 * 86400
    for note in (stale, stale_archived, fresh):
        os.utime(audio_store.mic_path(note), (old, old))
    for note in (stale, stale_archived):
        os.utime(audio_store.audio_path(note), (old, old))
    played_once = _with_audio(tmp_path / "played.md")
    os.utime(audio_store.audio_path(played_once), (old, old))
    os.utime(audio_store.mic_path(played_once), (old, old))
    audio_store.mark_played(played_once)

    assert audio_store.purge_unplayed([tmp_path, archive], now=now) == 2

    assert not audio_store.has_audio(stale)
    assert not audio_store.peaks_path(stale).exists()
    assert not audio_store.mic_path(stale).exists()
    assert not audio_store.has_audio(stale_archived)
    assert audio_store.mic_path(fresh).exists()
    assert audio_store.mic_path(played_once).exists()
    assert audio_store.has_audio(fresh)
    assert audio_store.has_audio(played_once)
    assert stale.exists() and stale_archived.exists()


def test_clean_temp_removes_dead_sessions_only(tmp_path):
    audio_dir = tmp_path / audio_store.AUDIO_DIRNAME
    dead = audio_dir / ".tmp-999999-crashed"
    mine = audio_dir / f".tmp-{os.getpid()}-live"
    for d in (dead, mine):
        d.mkdir(parents=True)
        (d / "0.pcm").write_bytes(b"\0\0")
    audio_store.clean_temp(tmp_path)
    assert not dead.exists()
    assert mine.exists()
