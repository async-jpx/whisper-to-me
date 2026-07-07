"""server.py note/search endpoints — none of these touch audio devices."""

from __future__ import annotations

from datetime import datetime

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


def _fake_config(**kwargs):
    from whisper_to_me.config import Config

    return lambda: Config(**kwargs)


def test_export_config_endpoint(client, monkeypatch, tmp_path):
    import whisper_to_me.server as server

    monkeypatch.setattr(server, "load_config", _fake_config())
    assert client.get("/api/export/config").json() == {
        "obsidian_vault": None,
        "notion_configured": False,
    }

    monkeypatch.setattr(
        server,
        "load_config",
        _fake_config(
            obsidian_vault=tmp_path / "vault",
            notion_token="ntn_super_secret",
            notion_database_id="d",
        ),
    )
    resp = client.get("/api/export/config")
    cfg = resp.json()
    assert cfg["obsidian_vault"].endswith("vault")
    assert cfg["notion_configured"] is True
    assert "ntn_super_secret" not in resp.text  # the token never goes over the wire


def test_copy_note_to_vault_endpoint(client, monkeypatch, tmp_path):
    import whisper_to_me.server as server

    monkeypatch.setattr(server, "load_config", _fake_config())
    assert client.post("/api/notes/note.md/vault").status_code == 400

    vault = tmp_path / "vault"
    monkeypatch.setattr(server, "load_config", _fake_config(obsidian_vault=vault))
    resp = client.post("/api/notes/note.md/vault")
    assert resp.status_code == 200
    copied = (vault / "note.md").read_text(encoding="utf-8")
    assert copied.startswith("---\n")  # frontmatter retrofitted on the way out
    assert copied.endswith(NOTE)


def test_vault_copy_of_live_journal_rejected(client, monkeypatch, tmp_path):
    import whisper_to_me.server as server

    monkeypatch.setattr(server, "load_config", _fake_config(obsidian_vault=tmp_path / "vault"))
    started = datetime(2026, 7, 6, 10, 0)
    live = notes.start_live_note("Standup", started, client.notes_dir)
    client.manager.state = "recording"
    client.manager.title = "Standup"
    client.manager.started = started
    assert client.post(f"/api/notes/{live.name}/vault").status_code == 409


def test_notion_endpoint(client, monkeypatch):
    import whisper_to_me.server as server
    from whisper_to_me import notion_export

    monkeypatch.setattr(server, "load_config", _fake_config())
    assert client.post("/api/notes/note.md/notion").status_code == 400

    monkeypatch.setattr(
        server, "load_config", _fake_config(notion_token="t", notion_database_id="d")
    )
    monkeypatch.setattr(
        notion_export, "push_note", lambda path, token, db: "https://www.notion.so/p"
    )
    resp = client.post("/api/notes/note.md/notion")
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://www.notion.so/p"

    def _boom(path, token, db):
        raise notion_export.NotionError("Notion API error (401): bad token")

    monkeypatch.setattr(notion_export, "push_note", _boom)
    resp = client.post("/api/notes/note.md/notion")
    assert resp.status_code == 502
    assert "bad token" in resp.json()["detail"]


@pytest.fixture()
def config_path(monkeypatch, tmp_path):
    """Redirect config reads/writes to a throwaway file so the settings
    endpoints exercise real save_config without touching ~/.config."""
    import whisper_to_me.config as config

    path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return path


def test_settings_get_defaults(client, config_path):
    assert client.get("/api/settings").json() == {
        "obsidian_vault": None,
        "notion_configured": False,
        "notion_database_id": None,
        "notion_token_set": False,
    }


def test_connect_and_disconnect_obsidian(client, config_path):
    resp = client.put("/api/settings/obsidian", json={"vault": "~/Vault/Meetings"})
    assert resp.status_code == 200
    assert resp.json()["obsidian_vault"].endswith("Vault/Meetings")
    # it landed in the real config file, so /api/export/config sees it too
    assert client.get("/api/export/config").json()["obsidian_vault"].endswith("Meetings")

    resp = client.delete("/api/settings/obsidian")
    assert resp.json()["obsidian_vault"] is None


def test_connect_obsidian_requires_path(client, config_path):
    assert client.put("/api/settings/obsidian", json={"vault": "  "}).status_code == 400


def test_connect_notion_stores_pair_without_leaking_token(client, config_path):
    resp = client.put(
        "/api/settings/notion", json={"token": "ntn_super_secret", "database_id": "db1"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "obsidian_vault": None,
        "notion_configured": True,
        "notion_database_id": "db1",
        "notion_token_set": True,
    }
    assert "ntn_super_secret" not in resp.text  # the token never goes over the wire


def test_connect_notion_keeps_saved_token_when_blank(client, config_path):
    client.put("/api/settings/notion", json={"token": "ntn_keep", "database_id": "db1"})
    # re-save with only a new database id and no token: the token is kept
    resp = client.put("/api/settings/notion", json={"database_id": "db2"})
    assert resp.status_code == 200
    assert resp.json()["notion_database_id"] == "db2"
    from whisper_to_me.config import load_config

    assert load_config(config_path).notion_token == "ntn_keep"


def test_connect_notion_requires_token_when_none_saved(client, config_path):
    resp = client.put("/api/settings/notion", json={"database_id": "db1"})
    assert resp.status_code == 400
    assert not load_config_configured(config_path)


def test_disconnect_notion_clears_pair(client, config_path):
    client.put("/api/settings/notion", json={"token": "t", "database_id": "d"})
    resp = client.delete("/api/settings/notion")
    assert resp.json()["notion_configured"] is False
    assert not load_config_configured(config_path)


def load_config_configured(path):
    from whisper_to_me.config import load_config

    return load_config(path).notion_configured


def test_scratchpad_rejected_when_idle(client):
    assert client.put("/api/session/scratchpad", json={"content": "x"}).status_code == 409
    assert client.get("/api/session/scratchpad").json() == {"content": ""}


def test_scratchpad_roundtrips_during_session(client):
    client.manager.state = "recording"
    resp = client.put("/api/session/scratchpad", json={"content": "decide launch date"})
    assert resp.status_code == 200
    assert client.get("/api/session/scratchpad").json() == {"content": "decide launch date"}
    # It lands on the sidecar file (crash-safety) but is never a note.
    assert (client.notes_dir / ".wtm-scratchpad.txt").read_text() == "decide launch date"
    assert [e["name"] for e in client.get("/api/notes").json()] == ["note.md"]


def test_scratchpad_too_large_rejected(client):
    client.manager.state = "recording"
    resp = client.put("/api/session/scratchpad", json={"content": "x" * 100_001})
    assert resp.status_code == 413
    assert client.get("/api/session/scratchpad").json() == {"content": ""}


def test_templates_endpoint(client):
    names = [t["name"] for t in client.get("/api/templates").json()]
    assert "standup" in names and "default" in names


def test_record_start_unknown_template_400(client):
    # Rejected before any recording starts, so no mic/transcriber is touched.
    resp = client.post("/api/record/start", json={"template": "nope"})
    assert resp.status_code == 400
    assert client.get("/api/status").json()["state"] == "idle"


def test_simulate_unknown_template_400(client, tmp_path):
    wav = tmp_path / "m.wav"
    wav.write_bytes(b"RIFF")  # existence is all the endpoint checks before template
    resp = client.post("/api/simulate", json={"mic": str(wav), "template": "nope"})
    assert resp.status_code == 400
    assert client.get("/api/status").json()["state"] == "idle"


def test_chat_empty_question_400(client):
    assert client.post("/api/chat", json={"question": "   "}).status_code == 400


def test_chat_happy_path(client, monkeypatch):
    import whisper_to_me.server as server

    monkeypatch.setattr(
        server.chat,
        "answer_question",
        lambda nd, q, model, history: {
            "answer": "yes [1]",
            "sources": [{"n": 1, "name": "note.md", "title": "Sprint Planning"}],
        },
    )
    out = client.post("/api/chat", json={"question": "what shipped?"}).json()
    assert out["answer"] == "yes [1]"
    assert out["sources"][0]["name"] == "note.md"


def test_chat_ollama_down_503(client, monkeypatch):
    import whisper_to_me.server as server
    from whisper_to_me import summarize as summ

    def boom(nd, q, model, history):
        raise summ.OllamaError("Cannot reach Ollama")

    monkeypatch.setattr(server.chat, "answer_question", boom)
    assert client.post("/api/chat", json={"question": "x"}).status_code == 503


def _drain(client_obj):
    import queue as _q

    events = []
    try:
        while True:
            events.append(client_obj.queue.get_nowait())
    except _q.Empty:
        pass
    return events


def test_brief_event_forwarded_but_not_buffered(client):
    mgr = client.manager
    c = mgr.add_client()
    _drain(c)  # discard the status/lines snapshot sent on connect
    mgr._sink(
        {"type": "brief", "name": "p.md", "title": "Prev", "modified": "x", "tldr": "we did X"}
    )
    events = _drain(c)
    assert any(e["type"] == "brief" and e["title"] == "Prev" for e in events)
    assert mgr._lines == []  # briefs are not added to the replay buffer


def test_followup_happy_path(client, monkeypatch):
    import whisper_to_me.server as server

    monkeypatch.setattr(
        server.followup, "draft_followup", lambda md, model: "Subject: Hi\n\nRecap."
    )
    out = client.post("/api/notes/note.md/followup").json()
    assert out["draft"].startswith("Subject:")


def test_followup_live_journal_409(client):
    started = datetime(2026, 7, 6, 10, 0)
    live = notes.start_live_note("Standup", started, client.notes_dir)
    client.manager.state = "recording"
    client.manager.title = "Standup"
    client.manager.started = started
    assert client.post(f"/api/notes/{live.name}/followup").status_code == 409


def test_followup_ollama_down_503(client, monkeypatch):
    import whisper_to_me.server as server
    from whisper_to_me import summarize as summ

    def boom(md, model):
        raise summ.OllamaError("down")

    monkeypatch.setattr(server.followup, "draft_followup", boom)
    assert client.post("/api/notes/note.md/followup").status_code == 503
