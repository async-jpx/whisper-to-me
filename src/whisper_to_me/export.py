"""Obsidian vault export — plain local file copies, no network.

New notes are vault-native already (save_note writes YAML frontmatter); this
module retrofits the back-catalog: a note without frontmatter gets one built
from its H1 title and the date encoded in its filename.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import notes

# save_note filenames: 2026-07-05-2336-daemon-application-discussion.md
_FILENAME_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})-")


def note_date(path: Path) -> datetime:
    m = _FILENAME_DATE_RE.match(path.name)
    if m:
        try:
            y, mo, d, h, mi = (int(g) for g in m.groups())
            return datetime(y, mo, d, h, mi)
        except ValueError:
            pass  # a slug that merely looks like a date (e.g. month 77)
    return datetime.fromtimestamp(path.stat().st_mtime)


def vault_ready_text(path: Path) -> str:
    """The note's content, guaranteed to start with YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    fm, body = notes.split_frontmatter(text)
    if fm is not None:
        return text
    return notes.frontmatter(notes.note_title(path), note_date(path)) + body


def copy_to_vault(path: Path, vault: Path, overwrite: bool = False) -> Path | None:
    """Copy one note into the vault (frontmatter ensured). Returns the
    destination, or None when it already exists and overwrite is False —
    never silently clobber a copy the user may have edited in Obsidian."""
    vault.mkdir(parents=True, exist_ok=True)
    dest = vault / path.name
    if dest.exists() and not overwrite:
        return None
    notes.write_note_text(dest, vault_ready_text(path))
    return dest


def export_obsidian(notes_dir: Path, vault: Path) -> tuple[list[str], list[str]]:
    """Back-catalog export: every note not already in the vault. Returns
    (copied names, skipped names)."""
    copied: list[str] = []
    skipped: list[str] = []
    for path in sorted(notes_dir.glob("*.md")):
        if copy_to_vault(path, vault) is None:
            skipped.append(path.name)
        else:
            copied.append(path.name)
    return copied, skipped
