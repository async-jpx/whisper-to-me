"""Markdown note storage — the local stand-in for a Notion page."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

DEFAULT_NOTES_DIR = Path.home() / "MeetingNotes"


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "meeting"


def note_path(title: str, started: datetime, notes_dir: Path = DEFAULT_NOTES_DIR) -> Path:
    return notes_dir / f"{started:%Y-%m-%d-%H%M}-{_slug(title)}.md"


def start_live_note(title: str, started: datetime, notes_dir: Path = DEFAULT_NOTES_DIR) -> Path:
    """Create the note file up front so transcript lines can be appended as
    they arrive — a crash or kill never loses what was already transcribed."""
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = note_path(title, started, notes_dir)
    path.write_text(
        f"# {title}\n\n*Recording started {started:%A %d %B %Y, %H:%M} — "
        "whisper-to-me (live)*\n\n## Transcript\n\n",
        encoding="utf-8",
    )
    return path


def append_line(path: Path, stamp: str, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"**[{stamp}]** {text}\n")


def save_note(
    title: str,
    transcript_lines: list[tuple[str, str]],
    summary: str | None,
    notes_dir: Path = DEFAULT_NOTES_DIR,
    started: datetime | None = None,
) -> Path:
    """Write a meeting note (summary + timestamped transcript) and return its path."""
    started = started or datetime.now()
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = note_path(title, started, notes_dir)

    body = [f"# {title}", "", f"*Recorded {started:%A %d %B %Y, %H:%M} — whisper-to-me*", ""]
    if summary:
        body += [summary, "", "---", ""]
    body += ["## Transcript", ""]
    body += [f"**[{ts}]** {text}" for ts, text in transcript_lines] or ["*(empty)*"]
    body.append("")

    path.write_text("\n".join(body), encoding="utf-8")
    return path
