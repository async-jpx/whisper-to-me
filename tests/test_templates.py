"""Meeting templates (Phase 4.4): discovery, user overrides, validation,
title-based suggestion, and that a template's sections reach the synthesis
prompt while the faithfulness rules stay last. Ollama is mocked."""

from __future__ import annotations

from datetime import datetime

import pytest

import whisper_to_me.summarize as summ
import whisper_to_me.templates as templates
from whisper_to_me.config import load_config, save_config

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


# -- user-managed templates + [templates] config (Hush redesign) --------------

VALID_BODY = "## Agenda\nBullets.\n\n## Action Items\n- [ ] task — owner\n"


def test_create_template_slugs_name_and_keeps_title():
    t = templates.create_template("Design Review!", 'Weekly "crit"\nsession', VALID_BODY)
    assert (t.name, t.title, t.builtin) == ("design-review", "Design Review!", False)
    assert t.description == 'Weekly "crit" session'
    assert t.sections == VALID_BODY.strip()
    assert t.path == templates.USER_TEMPLATES_DIR / "design-review.md"
    assert templates.load_template("design-review") == t


@pytest.mark.parametrize(
    "name, body",
    [
        ("ok", "## TL;DR\nno tasks\n"),
        ("ok", "## Action Items\nplain bullets only\n"),
        ("ok", "- [ ] a task but no heading\n"),
        ("!!!", VALID_BODY),
        ("   ", VALID_BODY),
    ],
)
def test_create_template_rejects_invalid(name, body):
    with pytest.raises(ValueError):
        templates.create_template(name, "", body)
    assert not templates.USER_TEMPLATES_DIR.exists() or not any(
        templates.USER_TEMPLATES_DIR.iterdir()
    )


def test_create_template_collides_with_builtins_and_users():
    with pytest.raises(FileExistsError):
        templates.create_template("Standup", "", VALID_BODY)
    templates.create_template("mine", "", VALID_BODY)
    with pytest.raises(FileExistsError):
        templates.create_template("MINE", "", VALID_BODY)


def test_delete_template_rules_and_config_pruning():
    with pytest.raises(PermissionError):
        templates.delete_template("standup")
    with pytest.raises(LookupError):
        templates.delete_template("nope")
    templates.create_template("mine", "", VALID_BODY)
    templates.set_favorite("mine", True)
    templates.set_favorite("standup", True)
    templates.set_default("mine")
    templates.delete_template("mine")
    assert templates.load_template("mine") is None
    cfg = load_config()
    assert cfg.favorite_templates == ("standup",)
    assert cfg.default_template is None


def test_favorites_toggle_and_persist():
    templates.set_favorite("standup", True)
    templates.set_favorite("interview", True)
    templates.set_favorite("standup", True)  # idempotent
    assert load_config().favorite_templates == ("interview", "standup")
    templates.set_favorite("interview", False)
    assert load_config().favorite_templates == ("standup",)
    with pytest.raises(LookupError):
        templates.set_favorite("nope", True)


def test_resolve_precedence():
    # nothing configured: suggestion, then None (= built-in default sections)
    assert templates.resolve_template(None, "Daily standup") == "standup"
    assert templates.resolve_template(None, "just a chat") is None
    templates.set_default("interview")
    # configured default beats the suggestion; an explicit pick beats both
    assert templates.resolve_template(None, "Daily standup") == "interview"
    assert templates.resolve_template("brainstorm", "Daily standup") == "brainstorm"
    with pytest.raises(LookupError):
        templates.set_default("nope")
    templates.set_default(None)
    assert load_config().default_template is None


def test_stale_configured_default_is_skipped():
    save_config({"default_template": "deleted-long-ago"})
    assert templates.resolve_template(None, "Daily standup") == "standup"
    assert templates.resolve_template(None, None) is None


def test_configured_default_reaches_summarizer(monkeypatch, tmp_path):
    import whisper_to_me.session as session

    seen: list = []

    def fake_summarize(text, model, context, user_notes, template):
        seen.append(template)
        return "## TL;DR\nx\n", None, {}

    monkeypatch.setattr(summ, "check_model", lambda m: True)
    monkeypatch.setattr(summ, "summarize_meeting", fake_summarize)
    lines = [("0:00:01", "hello")]
    now = datetime(2026, 10, 4, 9, 0)
    session.summarize_and_save("Daily standup", lines, now, tmp_path)
    templates.set_default("interview")
    session.summarize_and_save("Daily standup", lines, now, tmp_path)
    session.summarize_and_save("Daily standup", lines, now, tmp_path, template="brainstorm")
    assert seen == ["standup", "interview", "brainstorm"]


def test_open_prompt_uses_configured_default_over_suggestion():
    from whisper_to_me import runner

    now = datetime(2026, 10, 4, 9, 0)
    templates.set_default("interview")
    p = runner.open_prompt(runner.Idle(), "a1", "mic", "Daily standup", None, 0.0, now)
    assert p.prompt.template == "interview"
    p = runner.open_prompt(runner.Idle(), "a1", "mic", "Daily standup", "brainstorm", 0.0, now)
    assert p.prompt.template == "brainstorm"


def test_builtins_have_titles_and_default_sections_unchanged():
    by_name = {t.name: t for t in templates.list_templates()}
    assert by_name["default"].title == "General meeting"
    assert by_name["default"].sections == summ.SYNTH_SECTIONS
    assert all(t.title for t in by_name.values())


def test_concurrent_favorite_toggles_keep_every_favorite():
    import threading

    names = [t.name for t in templates.list_templates()]
    threads = [threading.Thread(target=templates.set_favorite, args=(n, True)) for n in names]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(load_config().favorite_templates) == sorted(names)


def test_quotes_in_title_and_description_round_trip():
    t = templates.create_template(
        'Review "Q3"', 'Says "hi"', "## Action Items\n\n- [ ] x\n"
    )
    loaded = templates.load_template(t.name)
    assert (loaded.title, loaded.description) == ('Review "Q3"', 'Says "hi"')
