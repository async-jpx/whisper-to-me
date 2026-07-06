"""config.py — tolerant parsing of the optional user config file."""

from __future__ import annotations

from pathlib import Path

from whisper_to_me.config import Config, load_config

FULL = """\
notes_dir = "~/Vault/Meetings"

[obsidian]
vault = "/tmp/vault"

[notion]
token = "ntn_secret"
database_id = "abc123"
"""


def test_missing_file_is_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg == Config()
    assert not cfg.notion_configured


def test_full_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(FULL, encoding="utf-8")
    cfg = load_config(path)
    assert cfg.notes_dir == Path("~/Vault/Meetings").expanduser()
    assert cfg.obsidian_vault == Path("/tmp/vault")
    assert cfg.notion_token == "ntn_secret"
    assert cfg.notion_database_id == "abc123"
    assert cfg.notion_configured


def test_partial_notion_is_not_configured(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[notion]\ntoken = "ntn_secret"\n', encoding="utf-8")
    assert not load_config(path).notion_configured


def test_malformed_toml_is_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("notes_dir = [unclosed", encoding="utf-8")
    assert load_config(path) == Config()


def test_wrong_types_are_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('notes_dir = 3\nobsidian = "not a table"\n', encoding="utf-8")
    assert load_config(path) == Config()
