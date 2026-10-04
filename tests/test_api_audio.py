"""Audio, transcript, NoteMeta, and recording-settings endpoints."""

from __future__ import annotations

import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from whisper_to_me import audio_store, notes
from whisper_to_me.server import ServerOptions, create_app

NOTE = """\
---
title: "Standup"
date: 2026-10-04T09:30
app: "Microsoft Teams"
tags: [meeting]
---
# Standup

## Transcript

**[0:00:03]** **You:** morning
**[0:04:10]** **Others:** hi
"""

AUDIO = bytes(range(256)) * 8  # 2 KiB stand-in for an m4a


class _Probe:
    def __init__(self, app=None):
        self.app = app

    def detect(self):
        return None

    def title_hint(self, trigger):
        return None

    def meeting_app(self, trigger):
        return self.app


@pytest.fixture()
def config_path(monkeypatch, tmp_path):
    import whisper_to_me.config as config

    path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return path


@pytest.fixture()
def client(tmp_path, config_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    note = notes_dir / "standup.md"
    note.write_text(NOTE, encoding="utf-8")
    audio_store.audio_dir(note).mkdir()
    audio_store.audio_path(note).write_bytes(AUDIO)
    audio_store.peaks_path(note).write_text('{"duration_s": 1.0, "peaks": [1.0]}')
    (notes_dir / "bare.md").write_text("# Bare\n", encoding="utf-8")
    probe = _Probe()
    app = create_app(ServerOptions(notes_dir=notes_dir), probe=probe)
    with TestClient(app) as c:
        c.notes_dir = notes_dir
        c.note = note
        c.probe = probe
        c.manager = app.state.manager
        yield c


def test_note_meta_fields(client):
    by_name = {n["name"]: n for n in client.get("/api/notes").json()}
    standup = by_name["standup.md"]
    assert {k: standup[k] for k in ("title", "date", "app", "has_audio", "duration_s")} == {
        "title": "Standup",
        "date": "2026-10-04T09:30:00",
        "app": "Microsoft Teams",
        "has_audio": True,
        "duration_s": 250,
    }
    bare = by_name["bare.md"]
    assert (bare["date"], bare["app"], bare["has_audio"], bare["duration_s"]) == (
        None, None, False, None,
    )


def test_transcript_endpoint(client):
    assert client.get("/api/notes/standup.md/transcript").json() == {
        "lines": [
            {"t": 3, "stamp": "0:00:03", "speaker": "You", "text": "morning"},
            {"t": 250, "stamp": "0:04:10", "speaker": "Others", "text": "hi"},
        ]
    }
    assert client.get("/api/notes/bare.md/transcript").json() == {"lines": []}
    assert client.get("/api/notes/nope.md/transcript").status_code == 404


def test_audio_supports_range_requests(client):
    resp = client.get("/api/notes/standup.md/audio", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 0-99/{len(AUDIO)}"
    assert resp.headers["content-type"] == "audio/mp4"
    assert resp.content == AUDIO[:100]

    full = client.get("/api/notes/standup.md/audio")
    assert full.status_code == 200
    assert full.content == AUDIO


def test_playing_resets_the_retention_clock(client):
    path = audio_store.audio_path(client.note)
    old = time.time() - 29 * 86400
    os.utime(path, (old, old))
    client.get("/api/notes/standup.md/audio", headers={"Range": "bytes=0-1"})
    assert path.stat().st_mtime > time.time() - 60


def test_audio_404s(client):
    assert client.get("/api/notes/bare.md/audio").status_code == 404
    assert client.get("/api/notes/bare.md/audio/peaks").status_code == 404
    assert client.get("/api/notes/..%2Fx.md/audio").status_code == 404


def test_peaks_endpoint(client):
    resp = client.get("/api/notes/standup.md/audio/peaks")
    assert resp.json() == {"duration_s": 1.0, "peaks": [1.0]}


def test_delete_audio_keeps_the_note(client):
    resp = client.delete("/api/notes/standup.md/audio")
    assert resp.status_code == 204
    assert client.note.exists()
    assert client.get("/api/notes/standup.md/audio").status_code == 404
    assert not audio_store.peaks_path(client.note).exists()
    assert client.delete("/api/notes/standup.md/audio").status_code == 204


def test_deleting_and_archiving_notes_take_the_audio(client):
    name = client.post("/api/notes/standup.md/archive").json()["name"]
    archived = notes.archive_dir(client.notes_dir) / name
    assert audio_store.has_audio(archived)
    assert [n["has_audio"] for n in client.get("/api/archived").json()] == [True]

    client.post(f"/api/archived/{name}/restore")
    assert client.get("/api/notes/standup.md/audio").status_code == 200

    client.delete("/api/notes/standup.md")
    assert not audio_store.audio_path(client.note).exists()


def test_recording_settings_round_trip(client, config_path):
    assert client.get("/api/settings").json()["recording"] == {"keep_audio": False}
    resp = client.put("/api/settings/recording", json={"keep_audio": True})
    assert resp.json()["recording"] == {"keep_audio": True}
    assert "[recording]\nkeep_audio = true" in config_path.read_text()
    assert client.get("/api/settings").json()["recording"] == {"keep_audio": True}

    resp = client.put("/api/settings/recording", json={"keep_audio": False})
    assert resp.json()["recording"] == {"keep_audio": False}
    assert "recording" not in config_path.read_text()
    assert client.put("/api/settings/recording", json={}).status_code == 422


def test_session_gets_keep_audio_and_the_meeting_app(client, monkeypatch):
    import whisper_to_me.server as server

    client.put("/api/settings/recording", json={"keep_audio": True})
    client.probe.app = "Zoom"
    seen = {}
    done = threading.Event()

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        seen["keep_audio"] = kwargs["keep_audio"]
        return [], kwargs["started"]

    def fake_summarize_and_save(title, lines, started, notes_dir, **kwargs):
        seen["app"] = kwargs["app"]
        done.set()
        return notes_dir / "unused.md"

    monkeypatch.setattr(server, "record_session", fake_record_session)
    monkeypatch.setattr(server, "summarize_and_save", fake_summarize_and_save)
    client.manager._transcriber = object()
    assert client.post("/api/record/start", json={}).status_code == 202
    assert done.wait(5)
    assert seen == {"keep_audio": True, "app": "Zoom"}
