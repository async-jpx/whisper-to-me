"""Markdown note storage — the local stand-in for a Notion page."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

DEFAULT_NOTES_DIR = Path.home() / "MeetingNotes"

# Must mirror what markdown-it-task-lists (the UI renderer) treats as a task
# item — any list item whose content starts with "[ ] "/"[x] "/"[X] " — so the
# UI's nth checkbox and toggle_task's nth match are the same line. (A literal
# "- [ ]" inside a fenced code block would skew the count; generated notes
# never contain fences.)
_TASK_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\] )")


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "meeting"


def note_path(title: str, started: datetime, notes_dir: Path = DEFAULT_NOTES_DIR) -> Path:
    return notes_dir / f"{started:%Y-%m-%d-%H%M}-{_slug(title)}.md"


def note_title(path: Path) -> str:
    """The note's H1 title, falling back to the filename stem."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return path.stem


def write_note_text(path: Path, content: str) -> None:
    """Replace a note's content atomically — a crash mid-write must never
    leave a half-written note (same promise the live journal makes)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def toggle_task(path: Path, index: int, checked: bool) -> bool:
    """Set the checked state of the index-th (0-based) `- [ ]` task item.
    Returns False when the note has fewer task items than that."""
    lines = path.read_text(encoding="utf-8").split("\n")
    seen = 0
    for i, line in enumerate(lines):
        if not _TASK_RE.match(line):
            continue
        if seen == index:
            mark = "x" if checked else " "
            lines[i] = _TASK_RE.sub(lambda m: f"{m.group(1)}{mark}{m.group(3)}", line, count=1)
            write_note_text(path, "\n".join(lines))
            return True
        seen += 1
    return False


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
