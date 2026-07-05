"""server.py note/search endpoints — none of these touch audio devices."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whisper_to_me import notes
from whisper_to_me.server import ServerOptions, _safe_note_path, create_app

NOTE = """\
# Sprint Planning

## Action Items

- [ ] Ship the exporter
- [ ] Update the roadmap

## Transcript

**[0:00:03]** hello there
"""


@pytest.fixture()
def client(tmp_path):
    (tmp_path / "note.md").write_text(NOTE, encoding="utf-8")
    app = create_app(ServerOptions(notes_dir=tmp_path))
    with TestClient(app) as client:
        client.notes_dir = tmp_path
        client.manager = app.state.manager
        yield client


def test_list_and_get_note(client):
    listing = client.get("/api/notes").json()
    assert [(e["name"], e["title"]) for e in listing] == [("note.md", "Sprint Planning")]
    assert client.get("/api/notes/note.md").text == NOTE


def test_get_unknown_note_404(client):
    assert client.get("/api/notes/nope.md").status_code == 404


def test_safe_note_path_rejects_traversal_and_non_md(tmp_path):
    assert _safe_note_path(tmp_path, "../evil.md") is None
    assert _safe_note_path(tmp_path, "sub/../../evil.md") is None
    assert _safe_note_path(tmp_path, ".wtm-index.sqlite3") is None
    assert _safe_note_path(tmp_path, "note.md.tmp") is None
    assert _safe_note_path(tmp_path, "fine.md") == (tmp_path / "fine.md").resolve()


def test_put_note_replaces_content(client):
    resp = client.put("/api/notes/note.md", json={"content": "# Renamed\n\nbody\n"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"
    assert client.get("/api/notes/note.md").text == "# Renamed\n\nbody\n"


def test_put_cannot_create_notes(client):
    assert client.put("/api/notes/new.md", json={"content": "x"}).status_code == 404
    assert not (client.notes_dir / "new.md").exists()


def test_patch_toggles_task(client):
    resp = client.patch("/api/notes/note.md", json={"task_index": 1, "checked": True})
    assert resp.status_code == 200
    assert "- [x] Update the roadmap" in client.get("/api/notes/note.md").text


def test_patch_bad_index_400(client):
    for index in (-1, 2):
        resp = client.patch("/api/notes/note.md", json={"task_index": index, "checked": True})
        assert resp.status_code == 400
    assert client.get("/api/notes/note.md").text == NOTE


def test_writes_to_live_journal_rejected(client):
    started = datetime(2026, 7, 6, 10, 0)
    live = notes.start_live_note("Standup", started, client.notes_dir)
    client.manager.state = "recording"
    client.manager.title = "Standup"
    client.manager.started = started

    for resp in (
        client.put(f"/api/notes/{live.name}", json={"content": "x"}),
        client.patch(f"/api/notes/{live.name}", json={"task_index": 0, "checked": True}),
    ):
        assert resp.status_code == 409

    # other notes stay editable while a session runs
    assert client.put("/api/notes/note.md", json={"content": "# Ok\n"}).status_code == 200


def test_search_endpoint(client):
    hits = client.get("/api/search", params={"q": "exporter"}).json()
    assert [h["name"] for h in hits] == ["note.md"]
    assert client.get("/api/search", params={"q": ""}).json() == []
