"""server.py note/search endpoints — none of these touch audio devices."""

from __future__ import annotations

import threading
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from whisper_to_me import notes, runner
from whisper_to_me.server import ServerOptions, _safe_note_path, create_app

NOTE = """\
# Sprint Planning

## Action Items

- [ ] Ship the exporter
- [ ] Update the roadmap

## Transcript

**[0:00:03]** hello there
"""


class ScriptedProbe:
    def __init__(self, trigger=None, hint=None):
        self.trigger = trigger
        self.hint = hint

    def detect(self):
        return self.trigger

    def title_hint(self, trigger):
        return self.hint


def _make_live(manager, title, started):
    plan = runner.manual_plan(title, None, started)
    manager._state = runner.Active(plan, "recording", started, threading.Event())


@pytest.fixture()
def client(tmp_path):
    (tmp_path / "note.md").write_text(NOTE, encoding="utf-8")
    app = create_app(ServerOptions(notes_dir=tmp_path), probe=ScriptedProbe())
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


def test_prompt_page_is_never_heuristically_cached(client):
    resp = client.get("/static/prompt.html")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
    assert "/api/prompts/" in resp.text


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
    _make_live(client.manager, "Standup", started)

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
    _make_live(client.manager, "Standup", started)
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
    _make_live(client.manager, "Standup", started)
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
    _make_live(client.manager, "Standup", started)
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
        "templates": {"default": None},
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
        "templates": {"default": None},
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
    _make_live(client.manager, "Standup", datetime(2026, 7, 6, 10, 0))
    resp = client.put("/api/session/scratchpad", json={"content": "decide launch date"})
    assert resp.status_code == 200
    assert client.get("/api/session/scratchpad").json() == {"content": "decide launch date"}
    # It lands on the sidecar file (crash-safety) but is never a note.
    assert (client.notes_dir / ".wtm-scratchpad.txt").read_text() == "decide launch date"
    assert [e["name"] for e in client.get("/api/notes").json()] == ["note.md"]


def test_scratchpad_too_large_rejected(client):
    _make_live(client.manager, "Standup", datetime(2026, 7, 6, 10, 0))
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


def _ui_message(role, text):
    return {"role": role, "parts": [{"type": "text", "text": text}]}


def _sse_events(resp):
    import json as _json

    events = []
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        events.append("[DONE]" if payload == "[DONE]" else _json.loads(payload))
    return events


def test_chat_stream_no_user_message_400(client):
    assert client.post("/api/chat/stream", json={"messages": []}).status_code == 400
    assert (
        client.post(
            "/api/chat/stream", json={"messages": [_ui_message("assistant", "hi")]}
        ).status_code
        == 400
    )


def test_chat_stream_happy_path(client, monkeypatch):
    import whisper_to_me.server as server

    seen = {}

    def prepare(nd, q, history=None):
        seen["question"] = q
        seen["history"] = history
        return "PROMPT", [
            {"n": 1, "name": "note.md", "title": "Sprint Planning"},
            {"n": 2, "name": "other.md", "title": "Standup"},
        ]

    monkeypatch.setattr(server.chat, "prepare", prepare)
    monkeypatch.setattr(
        server.summ, "_chat_stream", lambda model, system, user: iter(["yes ", "[1]"])
    )
    resp = client.post(
        "/api/chat/stream",
        json={
            "messages": [
                _ui_message("user", "earlier question"),
                _ui_message("assistant", "earlier answer"),
                _ui_message("user", "what shipped?"),
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert seen["question"] == "what shipped?"
    assert seen["history"] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    events = _sse_events(resp)
    deltas = [e["delta"] for e in events if e != "[DONE]" and e["type"] == "text-delta"]
    assert "".join(deltas) == "yes [1]"
    sources = next(e for e in events if e != "[DONE]" and e["type"] == "data-sources")
    assert sources["data"] == [{"n": 1, "name": "note.md", "title": "Sprint Planning"}]
    assert events[-1] == "[DONE]"
    assert {"type": "finish"} in events


def test_chat_stream_no_match(client, monkeypatch):
    import whisper_to_me.server as server

    monkeypatch.setattr(server.chat, "prepare", lambda nd, q, history=None: None)
    events = _sse_events(client.post(
        "/api/chat/stream", json={"messages": [_ui_message("user", "anything")]}
    ))
    deltas = [e["delta"] for e in events if e != "[DONE]" and e["type"] == "text-delta"]
    assert "".join(deltas) == server.chat.NO_MATCH


def test_chat_stream_ollama_down_emits_error_part(client, monkeypatch):
    import whisper_to_me.server as server

    def boom(model, system, user):
        raise server.summ.OllamaError("Cannot reach Ollama")
        yield  # pragma: no cover — make this a generator

    monkeypatch.setattr(
        server.chat, "prepare", lambda nd, q, history=None: ("PROMPT", [])
    )
    monkeypatch.setattr(server.summ, "_chat_stream", boom)
    events = _sse_events(client.post(
        "/api/chat/stream", json={"messages": [_ui_message("user", "x")]}
    ))
    err = next(e for e in events if e != "[DONE]" and e["type"] == "error")
    assert "Ollama" in err["errorText"]
    assert events[-1] == "[DONE]"


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
    _make_live(client.manager, "Standup", started)
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
    client.manager.stop()
    release.set()
    _wait_for_state(client, "idle")


def test_stop_record_while_idle_409(client):
    assert client.post("/api/record/stop").status_code == 409


def _wait_until(predicate, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


@pytest.fixture()
def detecting(tmp_path, monkeypatch):
    import whisper_to_me.server as server

    loads = []
    monkeypatch.setattr(
        server, "load_transcriber", lambda model, language: loads.append(model) or object()
    )
    probe = ScriptedProbe()

    def make(prompt_timeout: float = 30.0, poll: float = 0.01):
        opts = ServerOptions(notes_dir=tmp_path, poll=poll, prompt_timeout=prompt_timeout)
        app = create_app(opts, probe=probe)
        tc = TestClient(app)
        tc.__enter__()
        tc.manager = app.state.manager
        tc.probe = probe
        tc.loads = loads
        made.append(tc)
        return tc

    made = []
    yield make
    for tc in made:
        tc.__exit__(None, None, None)


def _status(tc):
    return tc.get("/api/status").json()


def _prompt(tc):
    _wait_until(lambda: _status(tc)["state"] == "prompting")
    return _status(tc)["prompt"]


def test_boot_is_idle_without_loading_whisper(detecting):
    tc = detecting()
    _wait_until(lambda: tc.manager._detector is not None)
    assert _status(tc) == {
        "type": "status", "state": "idle", "origin": None, "title": None,
        "started": None, "elapsed_s": None, "prompt": None,
    }
    assert tc.loads == []


def test_watch_endpoints_are_gone(client):
    for path in ("/api/watch/start", "/api/watch/stop", "/api/watch/respond"):
        assert client.post(path, json={"accept": True}).status_code == 404


def test_detected_meeting_prompts_with_a_countdown(detecting):
    tc = detecting(prompt_timeout=30.0)
    tc.probe.hint = "Weekly standup"
    tc.probe.trigger = "zoom"
    prompt = _prompt(tc)
    assert prompt["title"] == "Weekly standup" and prompt["trigger"] == "zoom"
    assert prompt["timeout_s"] == 30.0
    assert 0 < prompt["expires_in_s"] <= 30.0
    assert len(prompt["id"]) == 12
    status = _status(tc)
    assert (status["origin"], status["title"], status["started"]) == (None, None, None)
    _wait_until(lambda: tc.loads)

    ws = tc.manager.add_client()
    frame = ws.queue.get_nowait()
    assert frame["type"] == "status" and frame["prompt"]["id"] == prompt["id"]


def test_record_answer_starts_detected_session_and_stop_works(detecting, monkeypatch):
    import whisper_to_me.server as server

    calls = {}
    release = threading.Event()

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        calls["title"] = title
        calls["should_stop"] = kwargs["should_stop"]
        kwargs["events"](
            {"type": "status", "state": "recording", "title": title,
             "started": kwargs["started"].isoformat()}
        )
        assert kwargs["stop_event"].wait(timeout=10)
        assert release.wait(timeout=10)
        return [], kwargs["started"]

    saved = []
    monkeypatch.setattr(server, "record_session", fake_record_session)
    monkeypatch.setattr(
        server, "summarize_and_save", lambda *a, **kw: saved.append(kw) or a[3] / "x.md"
    )
    tc = detecting()
    tc.probe.hint = "Weekly standup"
    tc.probe.trigger = "zoom"
    first = _prompt(tc)

    assert tc.post(f"/api/prompts/{first['id']}", json={"answer": "record"}).status_code == 202
    _wait_until(lambda: _status(tc)["state"] == "recording")
    status = _status(tc)
    assert (status["origin"], status["title"], status["prompt"]) == (
        "detected", "Weekly standup", None,
    )
    assert calls["title"] == "Weekly standup"
    assert callable(calls["should_stop"])

    assert tc.post("/api/record/stop").status_code == 202
    assert _status(tc)["state"] == "stopping"
    release.set()
    _wait_until(lambda: _status(tc)["state"] == "idle")
    assert saved[0]["template"] == "standup" and saved[0]["auto_title"] is False

    import time

    time.sleep(0.2)
    assert _status(tc)["state"] == "idle"
    tc.probe.trigger = None
    time.sleep(0.1)
    tc.probe.trigger = "mic"
    assert _prompt(tc)["id"] != first["id"]


def test_unanswered_prompt_times_out_and_sits_out(detecting):
    import time

    tc = detecting(prompt_timeout=0.3)
    tc.probe.trigger = "zoom"
    prompt = _prompt(tc)
    _wait_until(lambda: _status(tc)["state"] == "idle", timeout=2.0)
    resp = tc.post(f"/api/prompts/{prompt['id']}", json={"answer": "record"})
    assert resp.status_code == 409 and resp.json()["detail"] == "prompt expired"
    time.sleep(0.5)
    assert _status(tc)["state"] == "idle"


def test_timeout_lands_on_the_deadline_not_the_next_poll(detecting):
    import time

    tc = detecting(prompt_timeout=0.3, poll=30.0)
    tc.probe.trigger = "zoom"
    tc.manager._wake.set()
    _prompt(tc)
    began = time.monotonic()
    _wait_until(lambda: _status(tc)["state"] == "idle", timeout=3.0)
    assert time.monotonic() - began < 2.0


def test_stale_answer_cannot_start_the_next_prompt(detecting):
    import time

    tc = detecting(prompt_timeout=0.3)
    tc.probe.trigger = "zoom"
    first = _prompt(tc)
    _wait_until(lambda: _status(tc)["state"] == "idle", timeout=2.0)
    tc.probe.trigger = None
    time.sleep(0.1)
    tc.probe.trigger = "zoom"
    second = _prompt(tc)
    assert second["id"] != first["id"]

    resp = tc.post(f"/api/prompts/{first['id']}", json={"answer": "record"})
    assert resp.status_code == 409
    status = _status(tc)
    assert status["state"] == "prompting" and status["prompt"]["id"] == second["id"]


def test_dismiss_goes_idle_at_once(detecting):
    tc = detecting()
    tc.probe.trigger = "mic"
    prompt = _prompt(tc)
    url = f"/api/prompts/{prompt['id']}"
    assert tc.post(url, json={"answer": "maybe"}).status_code == 422
    assert tc.post(url, json={"answer": "dismiss"}).status_code == 202
    assert _status(tc)["state"] == "idle"
    assert tc.post(url, json={"answer": "record"}).status_code == 409


def test_prompt_ends_when_the_meeting_does(detecting):
    tc = detecting()
    tc.probe.trigger = "zoom"
    _prompt(tc)
    tc.probe.trigger = None
    _wait_until(lambda: _status(tc)["state"] == "idle")


def test_manual_record_supersedes_a_prompt(detecting, monkeypatch):
    import whisper_to_me.server as server

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        assert kwargs["should_stop"] is None
        kwargs["events"](
            {"type": "status", "state": "recording", "title": title,
             "started": kwargs["started"].isoformat()}
        )
        kwargs["stop_event"].wait(timeout=10)
        return [], kwargs["started"]

    monkeypatch.setattr(server, "record_session", fake_record_session)
    monkeypatch.setattr(server, "summarize_and_save", lambda *a, **kw: a[3] / "x.md")
    tc = detecting()
    tc.probe.trigger = "zoom"
    prompt = _prompt(tc)

    assert tc.post("/api/record/start", json={"title": "Manual"}).status_code == 202
    status = _status(tc)
    assert status["origin"] == "manual" and status["prompt"] is None
    assert tc.post(f"/api/prompts/{prompt['id']}", json={"answer": "record"}).status_code == 409
    _wait_until(lambda: _status(tc)["state"] == "recording")
    assert tc.post("/api/record/stop").status_code == 202
    _wait_until(lambda: _status(tc)["state"] == "idle")


def test_simulate_cannot_be_stopped(client, monkeypatch):
    import whisper_to_me.server as server

    release = threading.Event()

    def fake_simulate_session(transcriber, title, notes_dir, mic_path, **kwargs):
        kwargs["events"](
            {"type": "status", "state": "recording", "title": title,
             "started": datetime.now().isoformat()}
        )
        assert release.wait(timeout=10)
        return [], datetime.now()

    monkeypatch.setattr(server, "simulate_session", fake_simulate_session)
    monkeypatch.setattr(server, "summarize_and_save", lambda *a, **kw: a[3] / "x.md")
    client.manager._transcriber = object()
    wav = client.notes_dir / "a.wav"
    wav.write_bytes(b"")
    assert client.post("/api/simulate", json={"mic": str(wav)}).status_code == 202
    _wait_for_state(client, "recording")
    assert client.get("/api/status").json()["origin"] == "simulate"
    assert client.post("/api/record/stop").status_code == 409
    release.set()
    _wait_for_state(client, "idle")


def test_shutdown_saves_the_active_session(tmp_path, monkeypatch):
    import time

    import whisper_to_me.server as server

    def fake_record_session(transcriber, title, notes_dir, **kwargs):
        kwargs["events"](
            {"type": "status", "state": "recording", "title": title,
             "started": kwargs["started"].isoformat()}
        )
        assert kwargs["stop_event"].wait(timeout=10)
        time.sleep(0.3)
        return [("0:00:01", "hello")], kwargs["started"]

    saved = []
    monkeypatch.setattr(server, "record_session", fake_record_session)
    monkeypatch.setattr(
        server, "summarize_and_save", lambda *a, **kw: saved.append(a[1]) or a[3] / "x.md"
    )
    app = create_app(ServerOptions(notes_dir=tmp_path), probe=ScriptedProbe())
    with TestClient(app) as tc:
        app.state.manager._transcriber = object()
        assert tc.post("/api/record/start", json={}).status_code == 202
        _wait_for_state(tc, "recording")
    assert saved == [[("0:00:01", "hello")]]
    assert app.state.manager.status()["state"] == "idle"


def test_a_failing_probe_does_not_end_detection(detecting):
    tc = detecting()
    failures = []

    def flaky():
        if len(failures) < 3:
            failures.append(1)
            raise OSError("pgrep: fork failed")
        return "zoom"

    tc.probe.detect = flaky
    assert _prompt(tc)["trigger"] == "zoom"
    assert len(failures) == 3


# -- templates API (Hush redesign) --------------------------------------------

USER_TEMPLATE = {
    "name": "Design Review",
    "description": "crit",
    "body": "## Notes\nx\n\n## Action Items\n- [ ] task\n",
}


def test_templates_list_shape(client):
    rows = {t["name"]: t for t in client.get("/api/templates").json()}
    std = rows["standup"]
    assert std["title"] == "Daily standup" and std["builtin"] is True
    assert std["favorite"] is False and "## Action Items" in std["body"]
    assert [n for n, t in rows.items() if t["is_default"]] == ["default"]


def test_template_create_delete_roundtrip(client):
    resp = client.post("/api/templates", json=USER_TEMPLATE)
    assert resp.status_code == 201
    created = resp.json()
    assert (created["name"], created["title"], created["builtin"]) == (
        "design-review", "Design Review", False,
    )
    assert client.post("/api/templates", json=USER_TEMPLATE).status_code == 409
    assert "design-review" in [t["name"] for t in client.get("/api/templates").json()]
    assert client.delete("/api/templates/design-review").status_code == 204
    assert client.delete("/api/templates/design-review").status_code == 404
    assert "design-review" not in [t["name"] for t in client.get("/api/templates").json()]


def test_template_create_validation(client):
    bad = dict(USER_TEMPLATE, body="## TL;DR\nno tasks\n")
    assert client.post("/api/templates", json=bad).status_code == 400
    assert client.post("/api/templates", json=dict(USER_TEMPLATE, name="??")).status_code == 400
    builtin = dict(USER_TEMPLATE, name="standup")
    assert client.post("/api/templates", json=builtin).status_code == 409


def test_builtin_template_delete_403(client):
    assert client.delete("/api/templates/standup").status_code == 403
    assert "standup" in [t["name"] for t in client.get("/api/templates").json()]


def test_template_favorite_and_default(client):
    resp = client.put("/api/templates/standup/favorite", json={"favorite": True})
    assert resp.status_code == 200 and resp.json()["favorite"] is True
    assert client.put("/api/templates/nope/favorite", json={"favorite": True}).status_code == 404

    resp = client.put("/api/settings/default-template", json={"name": "standup"})
    assert resp.json()["templates"] == {"default": "standup"}
    rows = {t["name"]: t for t in client.get("/api/templates").json()}
    assert rows["standup"]["is_default"] and not rows["default"]["is_default"]
    assert rows["standup"]["favorite"]

    assert client.put("/api/settings/default-template", json={"name": "nope"}).status_code == 400
    resp = client.put("/api/settings/default-template", json={"name": None})
    assert resp.json()["templates"] == {"default": None}
