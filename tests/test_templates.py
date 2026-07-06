"""Meeting templates (Phase 4.4): discovery, user overrides, validation,
title-based suggestion, and that a template's sections reach the synthesis
prompt while the faithfulness rules stay last. Ollama is mocked."""

from __future__ import annotations

import pytest

import whisper_to_me.summarize as summ
import whisper_to_me.templates as templates

BUILTINS = {"default", "one-on-one", "standup", "sales-call", "interview", "brainstorm"}


def test_builtin_discovery():
    found = templates.list_templates()
    assert BUILTINS <= {t.name for t in found}
    assert all(t.builtin for t in found)


def test_default_sections_match_summarize_constant():
    # The whole point of shipping default.md: "no template" == "default".
    assert templates.load_template("default").sections == summ.SYNTH_SECTIONS


def test_user_override_shadows_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "USER_TEMPLATES_DIR", tmp_path)
    (tmp_path / "standup.md").write_text(
        '---\nname: standup\ndescription: "mine"\nmatch: [daily]\n---\n'
        "## Action Items\n- [ ] do it\n",
        encoding="utf-8",
    )
    t = templates.load_template("standup")
    assert t.description == "mine"
    assert t.builtin is False
    assert "## Updates by Person" not in t.sections  # the builtin body is gone


def test_validation_rejects_body_without_action_items(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "USER_TEMPLATES_DIR", tmp_path)
    (tmp_path / "bad.md").write_text(
        "---\nname: bad\n---\n## TL;DR\njust a summary\n", encoding="utf-8"
    )
    assert "bad" not in {t.name for t in templates.list_templates()}
    assert templates.load_template("bad") is None


def test_suggest_template():
    assert templates.suggest_template("Weekly 1:1 with Sam") == "one-on-one"
    assert templates.suggest_template("Daily standup") == "standup"
    assert templates.suggest_template(None) is None
    assert templates.suggest_template("just a chat") is None


def test_frontmatter_missing_block_falls_back_to_stem(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "USER_TEMPLATES_DIR", tmp_path)
    (tmp_path / "plain.md").write_text("## Action Items\n- [ ] x\n", encoding="utf-8")
    t = templates.load_template("plain")
    assert t is not None and t.name == "plain" and t.match == ()


def _mock_ollama(monkeypatch) -> list:
    calls: list[tuple[str, str]] = []

    def chat(m, s, u, timeout=600, schema=None):
        calls.append((s, u))
        return "notes"

    def chat_json(m, s, u, schema):
        if schema is summ.TITLE_SCHEMA:
            return {"title": "T"}
        return {k: [] for k in summ.LIST_KEYS} | {"purpose": "", "action_items": []}

    monkeypatch.setattr(summ, "_chat", chat)
    monkeypatch.setattr(summ, "_chat_json", chat_json)
    return calls


def test_template_sections_reach_synthesis_and_rules_stay_last(monkeypatch):
    calls = _mock_ollama(monkeypatch)
    summ.summarize_meeting("**You:** hi\n", template="standup")
    synth_system = calls[-1][0]
    assert "## Updates by Person" in synth_system
    assert synth_system.rstrip().endswith(summ.SYNTH_RULES)


def test_template_none_is_identical_to_default(monkeypatch):
    calls = _mock_ollama(monkeypatch)
    summ.summarize_meeting("**You:** hi\n", template=None)
    none_system = calls[-1][0]
    calls.clear()
    summ.summarize_meeting("**You:** hi\n", template="default")
    assert none_system == calls[-1][0]


def test_unknown_template_raises():
    with pytest.raises(ValueError):
        summ.summarize_meeting("**You:** hi\n", template="nope")
