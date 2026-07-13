"""User configuration — ~/.config/whisper-to-me/config.toml.

Everything here is optional; the app runs fully without a config file. The
file is read fresh on each use (no caching) so connecting a vault or a Notion
database takes effect without restarting the daemon.

The file can be hand-edited, but the web UI also writes it (Settings →
Connections) via `save_config`, so users never have to touch TOML to connect
Obsidian or Notion — same on-disk format either way. Writing it does NOT add
any network path: the Notion token merely lands on local disk; it is only ever
sent by the sanctioned per-note push (see notion_export.py).

    notes_dir = "~/Vault/Meetings"      # save notes here (e.g. inside a vault)

    [watch]
    auto_start = true                   # daemon watches for meetings from boot
    confirm = true                      # ask (prompt) before recording; false
                                        # restores the old auto-record behavior

    [obsidian]
    vault = "~/Vault/Meetings"          # target for `wtm export` / Copy to vault

    [notion]                            # the ONE sanctioned network export:
    token = "ntn_..."                   # off unless both keys are set, and
    database_id = "..."                 # only ever pushed per-note by the user
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "whisper-to-me" / "config.toml"


@dataclass(frozen=True)
class Config:
    notes_dir: Path | None = None
    obsidian_vault: Path | None = None
    notion_token: str | None = None
    notion_database_id: str | None = None
    # None = unset (the caller's default applies, currently True for both).
    watch_auto_start: bool | None = None
    watch_confirm: bool | None = None

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_token and self.notion_database_id)


def _path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def _string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def load_config(path: Path | None = None) -> Config:
    """Parse the config file; a missing or malformed file is just defaults —
    a typo in the toml must never take recording down."""
    path = path or CONFIG_PATH
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return Config()
    obsidian = data.get("obsidian") or {}
    notion = data.get("notion") or {}
    watch = data.get("watch") or {}
    if not isinstance(obsidian, dict):
        obsidian = {}
    if not isinstance(notion, dict):
        notion = {}
    if not isinstance(watch, dict):
        watch = {}
    return Config(
        notes_dir=_path(data.get("notes_dir")),
        obsidian_vault=_path(obsidian.get("vault")),
        notion_token=_string(notion.get("token")),
        notion_database_id=_string(notion.get("database_id")),
        watch_auto_start=_bool(watch.get("auto_start")),
        watch_confirm=_bool(watch.get("confirm")),
    )


# -- writing (UI Settings → Connections) --------------------------------------
# The UI edits the same file hand-editors use. We keep a tiny TOML writer rather
# than pull in a dependency: the schema is small and fully string-valued, so the
# risk is low. Comments are not preserved on rewrite (documented in the UI).

# UI-editable field -> where it lives in the file. Top-level keys have no table.
_FIELDS: dict[str, tuple[str | None, str]] = {
    "notes_dir": (None, "notes_dir"),
    "obsidian_vault": ("obsidian", "vault"),
    "notion_token": ("notion", "token"),
    "notion_database_id": ("notion", "database_id"),
}


def _toml_str(value: str) -> str:
    """A TOML basic string — only the two escapes our values can contain."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(value: object) -> str:
    """Serialize one scalar; bools/numbers keep their TOML types so a
    hand-written `[watch] auto_start = false` survives a UI settings save."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _toml_str(str(value))


def _dump_toml(data: dict) -> str:
    """Serialize the config dict back to TOML. Handles top-level scalars and
    one level of tables (all our keys fit this), preserving any extra keys the
    user added; nested tables inside a table are dropped (we never write them)."""
    top: list[str] = []
    tables: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            if not value:
                continue
            body = [
                f"{k} = {_toml_value(v)}"
                for k, v in value.items()
                if not isinstance(v, dict)
            ]
            if body:
                tables.append(f"[{key}]\n" + "\n".join(body))
        else:
            top.append(f"{key} = {_toml_value(value)}")
    chunks = ["\n".join(top)] if top else []
    chunks.extend(tables)
    text = "\n\n".join(c for c in chunks if c)
    return text + "\n" if text else ""


def save_config(updates: dict[str, str | None], path: Path | None = None) -> Config:
    """Merge `updates` into the config file and write it back atomically.

    Keys are the Config field names in `_FIELDS`; a falsy/blank value clears
    that setting (and prunes a table that becomes empty). Other keys already in
    the file are preserved. The file may hold a Notion token, so it is written
    0600. Purely local disk I/O — this adds no network path."""
    path = path or CONFIG_PATH
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        data = {}

    for field, value in updates.items():
        table, key = _FIELDS[field]
        clean = value.strip() if isinstance(value, str) else None
        target = data
        if table is not None:
            existing = data.get(table)
            target = existing if isinstance(existing, dict) else {}
            data[table] = target
        if clean:
            target[key] = clean
        else:
            target.pop(key, None)
        if table is not None and not data[table]:
            del data[table]

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_dump_toml(data), encoding="utf-8")
    os.chmod(tmp, 0o600)  # holds a secret (Notion token) — keep it user-only
    os.replace(tmp, path)
    return load_config(path)
