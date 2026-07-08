"""config.py — tolerant parsing of the optional user config file."""

from __future__ import annotations

import stat
from pathlib import Path

from whisper_to_me.config import Config, load_config, save_config

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


# -- save_config (UI Settings → Connections) ---------------------------------


def test_save_creates_file_and_roundtrips(tmp_path):
    path = tmp_path / "sub" / "config.toml"  # parent dir created on write
    cfg = save_config({"obsidian_vault": "/tmp/vault"}, path)
    assert cfg.obsidian_vault == Path("/tmp/vault")
    assert load_config(path).obsidian_vault == Path("/tmp/vault")


def test_save_notion_pair(tmp_path):
    path = tmp_path / "config.toml"
    cfg = save_config(
        {"notion_token": "ntn_secret", "notion_database_id": "abc123"}, path
    )
    assert cfg.notion_configured
    assert cfg.notion_token == "ntn_secret"
    assert cfg.notion_database_id == "abc123"


def test_save_preserves_other_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(FULL, encoding="utf-8")
    save_config({"notion_token": "ntn_new", "notion_database_id": "db2"}, path)
    cfg = load_config(path)
    # untouched keys survive the rewrite
    assert cfg.notes_dir == Path("~/Vault/Meetings").expanduser()
    assert cfg.obsidian_vault == Path("/tmp/vault")
    assert cfg.notion_token == "ntn_new"


def test_save_preserves_unknown_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('custom = "keep me"\n[weird]\nk = "v"\n', encoding="utf-8")
    save_config({"obsidian_vault": "/tmp/v"}, path)
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["custom"] == "keep me"
    assert data["weird"] == {"k": "v"}
    assert data["obsidian"] == {"vault": "/tmp/v"}


def test_blank_value_clears_and_prunes_empty_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[obsidian]\nvault = "/tmp/vault"\n', encoding="utf-8")
    save_config({"obsidian_vault": ""}, path)
    assert load_config(path).obsidian_vault is None
    # the now-empty [obsidian] table is dropped entirely
    assert "obsidian" not in path.read_text(encoding="utf-8")


def test_save_disconnect_notion_clears_both(tmp_path):
    path = tmp_path / "config.toml"
    save_config({"notion_token": "t", "notion_database_id": "d"}, path)
    save_config({"notion_token": None, "notion_database_id": None}, path)
    assert not load_config(path).notion_configured
    assert path.read_text(encoding="utf-8") == ""  # nothing left


def test_save_escapes_special_characters(tmp_path):
    path = tmp_path / "config.toml"
    weird = '/tmp/va"ult\\backslash'
    save_config({"obsidian_vault": weird}, path)
    assert load_config(path).obsidian_vault == Path(weird)


def test_save_file_is_user_only(tmp_path):
    path = tmp_path / "config.toml"
    save_config({"notion_token": "secret", "notion_database_id": "d"}, path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600  # holds a secret; not group/world readable


def test_save_over_malformed_file_starts_clean(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("notes_dir = [unclosed", encoding="utf-8")
    cfg = save_config({"obsidian_vault": "/tmp/v"}, path)
    assert cfg.obsidian_vault == Path("/tmp/v")
