"""On-demand coaching uses conversation context and only the user's lines."""

import json

from fastapi.testclient import TestClient

from whisper_to_me import audio_store, summarize
from whisper_to_me.server import ServerOptions, create_app


def _client(tmp_path):
    (tmp_path / "meeting.md").write_text(
        "# Meeting\n\n## Transcript\n\n"
        "**[0:00:01]** **Others:** What is the launch plan?\n"
        "**[0:00:04]** **You:** We should do it soon.\n"
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
    assert response.json()["audio_metrics"]["longer_low_energy_gaps"] == 1
    assert "You: We should do it soon." in seen["prompt"]
    assert "OTHER SPEAKER Others: What is the launch plan?" in seen["prompt"]
    assert "never evidence of the user's communication" in seen["system"]
