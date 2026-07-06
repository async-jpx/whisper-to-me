"""User configuration — ~/.config/whisper-to-me/config.toml, read-only.

Everything here is optional; the app runs fully without a config file. The
file is read fresh on each use (no caching) so pasting a Notion token or a
vault path takes effect without restarting the daemon.

    notes_dir = "~/Vault/Meetings"      # save notes here (e.g. inside a vault)

    [obsidian]
    vault = "~/Vault/Meetings"          # target for `wtm export` / Copy to vault

    [notion]                            # the ONE sanctioned network export:
    token = "ntn_..."                   # off unless both keys are set, and
    database_id = "..."                 # only ever pushed per-note by the user
"""

from __future__ import annotations

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
    if not isinstance(obsidian, dict):
        obsidian = {}
    if not isinstance(notion, dict):
        notion = {}
    return Config(
        notes_dir=_path(data.get("notes_dir")),
        obsidian_vault=_path(obsidian.get("vault")),
        notion_token=_string(notion.get("token")),
        notion_database_id=_string(notion.get("database_id")),
    )
