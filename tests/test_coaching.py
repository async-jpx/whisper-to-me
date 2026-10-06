"""Coaching: on-demand reviews use conversation context and only the user's lines; saved reviews and transcript stats feed the dashboard."""

import json
import shutil
import subprocess
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from whisper_to_me import audio_store, summarize
from whisper_to_me.server import ServerOptions, create_app


def _client(tmp_path):
    (tmp_path / "meeting.md").write_text(
        "# Meeting\n\n## Transcript\n\n"
        "**[0:00:01]** **Others:** What is the launch plan?\n"
        "**[0:00:04]** **You:** We should do it soon. " + "Really soon. " * 8 + "\n"
        "**[0:00:08]** **Others:** Which day?\n",
        encoding="utf-8",
    )
    return TestClient(create_app(ServerOptions(notes_dir=tmp_path)))


def test_analyze_uses_full_conversation_without_audio(tmp_path, monkeypatch):
    seen = {}

    def answer(model, system, user, schema):
        seen["prompt"] = user
        return {
            "what_worked": "You proposed a launch.",
            "improvement": "Give a date.",
            "try_saying": "Let's launch Thursday.",
            "delivery": "You sounded confident.",
        }

    monkeypatch.setattr(summarize, "_chat_json", answer)
    with _client(tmp_path) as client:
        response = client.post("/api/notes/meeting.md/analyze", json={"line_index": 1})
    assert response.status_code == 200
    assert "Which day?" in seen["prompt"]
    assert "What is the launch plan?" in seen["prompt"]
    assert response.json()["improvement"] == "Give a date."
    assert response.json()["audio_metrics"] is None
    assert "cannot be judged" in response.json()["delivery"]


def test_analyze_rejects_other_speaker_and_invalid_index(tmp_path):
    with _client(tmp_path) as client:
        assert client.post("/api/notes/meeting.md/analyze", json={"line_index": 0}).status_code == 400
        assert client.post("/api/notes/meeting.md/analyze", json={"line_index": 99}).status_code == 400
        assert client.post("/api/notes/missing.md/analyze", json={"line_index": 1}).status_code == 404


def test_analyze_model_failure_is_503(tmp_path, monkeypatch):
    def fail(*args):
        raise summarize.OllamaError("Ollama unavailable")

    monkeypatch.setattr(summarize, "_chat_json", fail)
    with _client(tmp_path) as client:
        response = client.post("/api/notes/meeting.md/analyze", json={"line_index": 1})
    assert response.status_code == 503
    assert "Ollama unavailable" in response.json()["detail"]


def test_meeting_analysis_serializes_waveform_metrics(tmp_path, monkeypatch):
    seen = {}

    def answer(model, system, user, schema):
        seen["system"] = system
        seen["prompt"] = user
        return {
            "overall_read": "Clear and engaged.",
            "strengths": [],
            "patterns": [],
            "rewrites": [],
            "next_time": {"practice": "Keep building on that.", "steps": [], "structure": ""},
        }

    monkeypatch.setattr(summarize, "_chat_json", answer)
    with _client(tmp_path) as client:
        note = tmp_path / "meeting.md"
        audio_store.audio_dir(note).mkdir()
        audio_store.audio_path(note).write_bytes(b"test audio")
        peaks = [0.8] * 40
        peaks[10:13] = [0.0] * 3
        audio_store.peaks_path(note).write_text(
            json.dumps({"duration_s": 20, "peaks": peaks}), encoding="utf-8"
        )
        response = client.post("/api/notes/meeting.md/analyze/meeting")
    assert response.status_code == 200
    assert response.json()["review"]["audio_metrics"]["longer_low_energy_gaps"] == 1
    assert "You: We should do it soon." in seen["prompt"]
    assert "OTHER SPEAKER Others: What is the launch plan?" in seen["prompt"]
    assert "never evidence of the user's communication" in seen["system"]


def test_meeting_review_is_saved_and_feeds_the_dashboard(tmp_path, monkeypatch):
    review = {
        "overall_read": "Clear.",
        "strengths": [],
        "patterns": [{"pattern": "Vague timing", "evidence": [2], "impact": "", "change": ""}],
        "rewrites": [],
        "next_time": {"practice": "Name a date.", "steps": [], "structure": ""},
    }
    monkeypatch.setattr(summarize, "_chat_json", lambda *args: review)
    with _client(tmp_path) as client:
        assert client.get("/api/notes/meeting.md/analyze/meeting").status_code == 404
        assert client.post("/api/notes/meeting.md/analyze/meeting").status_code == 200
        saved = client.get("/api/notes/meeting.md/analyze/meeting").json()
        dashboard = client.get("/api/coaching/dashboard").json()
    assert saved["review"]["next_time"]["practice"] == "Name a date."
    assert [p.name for p in tmp_path.glob("*.md")] == ["meeting.md"]
    [meeting] = dashboard["meetings"]
    assert meeting["focus"] == "Name a date."
    assert meeting["patterns"] == ["Vague timing"]
    assert meeting["stats"]["your_words"] == 21
    assert meeting["stats"]["talk_share"] == round(21 / 28, 3)


def test_meeting_stats_merge_turns_and_count_fillers():
    from whisper_to_me import notes
    from whisper_to_me.coaching.stats import meeting_stats

    lines = notes.parse_transcript(
        "## Transcript\n\n"
        "**[0:00:01]** **You:** Um, so I mean we ship?\n"
        "**[0:00:03]** **You:** Friday works.\n"
        "**[0:00:05]** **Others:** Sure.\n"
        "**[0:00:07]** **You:** Great. " + "Yes. " * 11 + "\n"
    )
    stats = meeting_stats(lines)
    assert stats["turns"] == 2
    assert stats["longest_turn_words"] == 12
    assert stats["questions"] == 1
    assert stats["fillers_per_100_words"] == round(2 * 100 / 20, 2)
    assert meeting_stats(notes.parse_transcript("## Transcript\n\n**[0:00:01]** **Others:** Hi.\n")) is None
    assert meeting_stats(notes.parse_transcript("## Transcript\n\n**[0:00:01]** **You:** Hi.\n")) is None


def test_dashboard_skips_notes_without_your_speech(tmp_path):
    (tmp_path / "other.md").write_text(
        "# Other\n\n## Transcript\n\n**[0:00:01]** **Others:** Hello.\n", encoding="utf-8"
    )
    with TestClient(create_app(ServerOptions(notes_dir=tmp_path))) as client:
        dashboard = client.get("/api/coaching/dashboard").json()
    assert dashboard["meetings"] == []
    assert dashboard["trends"]["talk_share"] == {"recent": None, "earlier": None}


def _voiced(seconds: float, f0: float = 120.0) -> np.ndarray:
    t = np.arange(int(seconds * 16_000)) / 16_000
    return sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, 12)).astype(np.float32) * 0.1


def _m4a(path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = path.with_suffix(".wav")
    with wave.open(str(wav_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(wav_path), str(path)],
                   check=True, capture_output=True)
    wav_path.unlink()


TURN = "**[0:00:00]** **You:** " + "word " * 30 + "\n**[0:00:09]** **Others:** Thanks.\n"


def test_measure_counts_pauses_pitch_and_pace():
    from whisper_to_me import notes
    from whisper_to_me.coaching import voice

    # 3 s voiced, 1 s silent, 3 s voiced at a higher pitch; 30 words over 7 s.
    samples = np.concatenate([_voiced(3, 110), np.zeros(16_000, np.float32), _voiced(3, 220)])
    lines = notes.parse_transcript("## Transcript\n\n" + TURN)
    m = voice.measure(samples, lines)
    assert m["pauses_per_minute"] == pytest.approx(60 / 7, abs=0.2)
    assert m["speaking_rate_wpm"] == pytest.approx(30 * 60 / 7, abs=3)
    assert m["pitch_range_semitones"] == pytest.approx(12, abs=1)
    assert 100 <= m["pitch_median_hz"] <= 230
    assert voice.measure(_voiced(2), lines) is None  # under MIN_SPEECH_S


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert")
def test_voice_uses_the_mic_track_and_caches(tmp_path, monkeypatch):
    from whisper_to_me.coaching import voice

    note = tmp_path / "call.md"
    note.write_text("# Call\n\n## Transcript\n\n" + TURN, encoding="utf-8")
    _m4a(audio_store.audio_path(note), _voiced(9))
    assert voice.voice_metrics(note) is None  # mixed only: whose voice is unknown
    _m4a(audio_store.mic_path(note), _voiced(8, 150))
    measured = voice.voice_metrics(note)
    assert 140 <= measured["pitch_median_hz"] <= 160

    monkeypatch.setattr(voice, "decode", lambda path: pytest.fail("cache should answer"))
    assert voice.voice_metrics(note) == measured
    audio_store.delete_audio(note)
    assert voice.voice_metrics(note) == measured  # outlives the audio purge


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert")
def test_single_source_recording_is_your_voice(tmp_path):
    from whisper_to_me.coaching import voice

    note = tmp_path / "solo.md"
    note.write_text(
        "# Solo\n\n## Transcript\n\n**[0:00:00]** " + "word " * 30 + "\n", encoding="utf-8"
    )
    _m4a(audio_store.audio_path(note), _voiced(8))
    assert voice.voice_metrics(note)["speaking_rate_wpm"] > 0


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert")
def test_meeting_review_and_dashboard_use_your_microphone(tmp_path, monkeypatch):
    note = tmp_path / "call.md"
    note.write_text("# Call\n\n## Transcript\n\n" + TURN, encoding="utf-8")
    _m4a(audio_store.audio_path(note), _voiced(9))
    _m4a(audio_store.mic_path(note), _voiced(8))
    seen = {}

    def answer(model, system, user, schema):
        seen["prompt"] = user
        return {"overall_read": "", "strengths": [], "patterns": [], "rewrites": [],
                "next_time": {"practice": "", "steps": [], "structure": ""}}

    monkeypatch.setattr(summarize, "_chat_json", answer)
    with TestClient(create_app(ServerOptions(notes_dir=tmp_path))) as client:
        review = client.post("/api/notes/call.md/analyze/meeting").json()["review"]
        [meeting] = client.get("/api/coaching/dashboard").json()["meetings"]
    assert review["audio_metrics"]["source"] == "your microphone"
    assert "pitch_range_semitones" in seen["prompt"]
    assert meeting["stats"]["speaking_rate_wpm"] == review["audio_metrics"]["speaking_rate_wpm"]


def test_interruptions_and_residual_echo_do_not_bend_your_voice_stats():
    from whisper_to_me import notes
    from whisper_to_me.coaching import voice

    # You speak 0–8 s; Others' line at 2 s talks over you (absent from your
    # track); 1 s after you stop, residual echo of Others 28 dB under your
    # voice: above the fixed floor, so only the gate relative to you drops it.
    samples = np.concatenate([
        _voiced(8, 150) * 5, np.zeros(16_000, np.float32), _voiced(4, 110) * 5 * 0.04,
    ])
    lines = notes.parse_transcript(
        "## Transcript\n\n**[0:00:00]** **You:** " + "word " * 24 +
        "\n**[0:00:02]** **Others:** Can I jump in?\n**[0:00:09]** **Others:** Sure.\n"
    )
    m = voice.measure(samples, lines)
    assert m["speaking_seconds"] == 8
    assert m["speaking_rate_wpm"] == pytest.approx(180, abs=3)
    assert m["pauses_per_minute"] == 0
    assert m["pitch_median_hz"] == pytest.approx(150, abs=5)
