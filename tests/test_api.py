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
    # auto_watch off: these tests exercise endpoints from a known-idle daemon
    # (the auto-watch default gets its own dedicated test below).
    app = create_app(ServerOptions(notes_dir=tmp_path, auto_watch=False))
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


def test_delete_note(client):
    assert client.delete("/api/notes/note.md").status_code == 200
    assert not (client.notes_dir / "note.md").exists()
    assert client.get("/api/notes").json() == []


def test_delete_unknown_note_404(client):
    assert client.delete("/api/notes/nope.md").status_code == 404


def test_delete_live_journal_rejected(client):
    started = datetime(2026, 7, 6, 10, 0)
    live = notes.start_live_note("Standup", started, client.notes_dir)
    client.manager.state = "recording"
    client.manager.title = "Standup"
    client.manager.started = started
    assert client.delete(f"/api/notes/{live.name}").status_code == 409
    assert live.exists()


def test_archive_restore_roundtrip(client):
    resp = client.post("/api/notes/note.md/archive")
    assert resp.status_code == 200
    # gone from the active listing, no longer readable as a note
    assert client.get("/api/notes").json() == []
    assert client.get("/api/notes/note.md").status_code == 404
    # the file itself moved into the Archive subfolder, intact
    assert (client.notes_dir / "Archive" / "note.md").read_text(encoding="utf-8") == NOTE
    # and it shows up in the archived listing
    archived = client.get("/api/archived").json()
    assert [(e["name"], e["title"]) for e in archived] == [("note.md", "Sprint Planning")]

    # restore brings it back to the active notes
    assert client.post("/api/archived/note.md/restore").status_code == 200
    assert [e["name"] for e in client.get("/api/notes").json()] == ["note.md"]
    assert client.get("/api/archived").json() == []


def test_archive_live_journal_rejected(client):
    started = datetime(2026, 7, 6, 10, 0)
    live = notes.start_live_note("Standup", started, client.notes_dir)
    client.manager.state = "recording"
    client.manager.title = "Standup"
    client.manager.started = started
    assert client.post(f"/api/notes/{live.name}/archive").status_code == 409
    assert live.exists()


def test_delete_archived_note(client):
    assert client.post("/api/notes/note.md/archive").status_code == 200
    assert client.delete("/api/archived/note.md").status_code == 200
    assert not (client.notes_dir / "Archive" / "note.md").exists()
    assert client.get("/api/archived").json() == []


def test_archived_endpoints_404_unknown(client):
    assert client.post("/api/archived/nope.md/restore").status_code == 404
    assert client.delete("/api/archived/nope.md").status_code == 404


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


# ---------- record lifecycle (record_session is faked: no audio devices) ----


def _wait_for_state(client, state: str, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/api/status").json()["state"] == state:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"daemon never reached {state!r} (at {client.get('/api/status').json()['state']!r})"
    )


def test_record_lifecycle_states(client, monkeypatch):
    """start -> starting/recording; stop -> stopping (immediately, while the
    session drains) -> idle. Repeat stops are no-ops; starts stay rejected
    until idle. This is the contract the UI's button feedback relies on."""
    import threading

    import whisper_to_me.server as server

    release = threading.Event()

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        kwargs["events"](
            {
                "type": "status",
                "state": "recording",
                "title": title,
                "started": kwargs["started"].isoformat(),
            }
        )
        assert kwargs["stop_event"].wait(timeout=10)
        # Hold the session in its wind-down until the test releases it, so
        # the "stopping" state is observable without sleeps.
        assert release.wait(timeout=10)
        return [], kwargs["started"]

    def fake_summarize_and_save(title, transcript_lines, started, notes_dir, **kwargs):
        return notes_dir / "unused.md"

    monkeypatch.setattr(server, "record_session", fake_record_session)
    monkeypatch.setattr(server, "summarize_and_save", fake_summarize_and_save)
    client.manager._transcriber = object()  # skip the Whisper model load

    assert client.post("/api/record/start", json={}).status_code == 202
    _wait_for_state(client, "recording")
    assert client.post("/api/record/start", json={}).status_code == 409

    assert client.post("/api/record/stop").status_code == 202
    assert client.get("/api/status").json()["state"] == "stopping"
    # while draining: another stop is a friendly no-op, a start is still busy
    assert client.post("/api/record/stop").status_code == 202
    assert client.post("/api/record/start", json={}).status_code == 409

    release.set()
    _wait_for_state(client, "idle")
    # the daemon is reusable afterwards: a new start is accepted again
    assert client.post("/api/record/start", json={}).status_code == 202
    _wait_for_state(client, "recording")
    client.manager.stop_record()
    release.set()
    _wait_for_state(client, "idle")


def test_stop_record_while_idle_409(client):
    assert client.post("/api/record/stop").status_code == 409


def test_manual_record_preempts_watch_and_resumes(client, monkeypatch):
    """With watch-by-default, 'New meeting' must not 409: an idle watch
    yields to the manual recording and re-arms itself once the note is
    saved."""
    import whisper_to_me.server as server

    watch_runs = []

    def fake_watch_loop(get_transcriber, opts, events=None, stop_event=None, **kwargs):
        watch_runs.append(True)
        events({"type": "status", "state": "watching", "title": None, "started": None})
        stop_event.wait(10)

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        kwargs["events"](
            {
                "type": "status",
                "state": "recording",
                "title": title,
                "started": kwargs["started"].isoformat(),
            }
        )
        kwargs["stop_event"].wait(10)
        return [], kwargs["started"]

    monkeypatch.setattr(server, "watch_loop", fake_watch_loop)
    monkeypatch.setattr(server, "record_session", fake_record_session)
    monkeypatch.setattr(
        server, "summarize_and_save", lambda *a, **kw: a[3] / "unused.md"
    )
    client.manager._transcriber = object()  # skip the Whisper model load

    assert client.post("/api/watch/start").status_code == 202
    _wait_for_state(client, "watching")
    assert len(watch_runs) == 1

    # New meeting while watching: preempts instead of 409ing
    assert client.post("/api/record/start", json={"title": "Manual"}).status_code == 202
    _wait_for_state(client, "recording")

    # …and stopping the manual recording re-arms the watch
    assert client.post("/api/record/stop").status_code == 202
    _wait_for_state(client, "watching")
    assert len(watch_runs) == 2

    assert client.post("/api/watch/stop").status_code == 202
    _wait_for_state(client, "idle")


# ---------- watch prompt flow (watch_loop is faked: no audio devices) ----


def test_watch_respond_while_idle_409(client):
    assert client.post("/api/watch/respond", json={"accept": True}).status_code == 409


def test_watch_prompt_accept_roundtrip(client, monkeypatch):
    """watch start -> prompting; /api/watch/respond hands the decision to the
    loop; stop returns the daemon to idle and expires the prompt."""
    import threading
    import time

    import whisper_to_me.server as server

    got = {}
    answered = threading.Event()

    def fake_watch_loop(
        get_transcriber, opts, events=None, stop_event=None,
        scratchpad=None, clear_scratchpad=None, decision=None,
    ):
        got["confirm"] = opts.confirm
        events({"type": "status", "state": "prompting", "title": "Standup", "started": None})
        deadline = time.monotonic() + 5
        choice = None
        while choice is None and time.monotonic() < deadline:
            choice = decision()
            time.sleep(0.01)
        got["choice"] = choice
        answered.set()
        stop_event.wait(5)

    monkeypatch.setattr(server, "watch_loop", fake_watch_loop)

    assert client.post("/api/watch/start").status_code == 202
    _wait_for_state(client, "prompting")
    status = client.get("/api/status").json()
    assert status["mode"] == "watch" and status["title"] == "Standup"

    assert client.post("/api/watch/respond", json={"accept": True}).status_code == 202
    assert answered.wait(5)
    assert got["choice"] == "accept"
    assert got["confirm"] is True  # the daemon default asks before recording

    assert client.post("/api/watch/stop").status_code == 202
    _wait_for_state(client, "idle")
    assert client.post("/api/watch/respond", json={"accept": True}).status_code == 409


def test_auto_watch_starts_on_startup(tmp_path, monkeypatch):
    """The daemon's resting state is watching: create_app with the default
    auto_watch starts a watch session during startup — without loading the
    Whisper model (get_transcriber must stay uncalled until a recording)."""
    import threading

    import whisper_to_me.server as server

    started = threading.Event()
    transcriber_loads = []

    def fake_watch_loop(
        get_transcriber, opts, events=None, stop_event=None, **kwargs
    ):
        transcriber_loads.append(get_transcriber)  # captured, never called
        events({"type": "status", "state": "watching", "title": None, "started": None})
        started.set()
        stop_event.wait(5)

    monkeypatch.setattr(server, "watch_loop", fake_watch_loop)
    app = create_app(ServerOptions(notes_dir=tmp_path))  # auto_watch default: on
    with TestClient(app) as client:
        assert started.wait(5)
        assert client.get("/api/status").json()["state"] == "watching"
        assert app.state.manager._transcriber is None  # Whisper not loaded
        assert client.post("/api/watch/stop").status_code == 202
        _wait_for_state(client, "idle")
