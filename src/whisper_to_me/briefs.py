"""Meeting briefs: when a meeting starts with a known title, surface the most
recent related note — "Last time you discussed…". Local FTS retrieval only,
best-effort: any failure returns None and never disturbs a recording.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import notes, search


def _tldr(md: str) -> str:
    """The note's TL;DR, whitespace-collapsed and clamped; falling back to the
    first real paragraph after the H1, or "" when neither exists."""
    _, body = notes.split_frontmatter(md)
    m = re.search(r"^##\s+TL;DR\s*$", body, flags=re.MULTILINE)
    if m:
        rest = body[m.end() :]
        nxt = re.search(r"^##\s+", rest, flags=re.MULTILINE)
        text = rest[: nxt.start()] if nxt else rest
    else:
        text = ""
        for para in re.split(r"\n\s*\n", body):
            p = para.strip()
            if not p or p.startswith("#") or p.startswith("*Record"):
                continue
            text = p
            break
    return " ".join(text.split())[:400]


def find_brief(notes_dir: Path, title: str, exclude: str | None = None) -> dict | None:
    """The best prior note related to `title` (most recent among the top FTS
    matches), or None. `exclude` drops one note by filename (e.g. the meeting
    being recorded right now). Never raises."""
    if not title:
        return None
    try:
        # OR-match: a recurring meeting rarely repeats its exact title (times
        # differ), but shares the salient words; ranking + recency pick the
        # relevant prior note.
        hits = search.search_notes(notes_dir, title, limit=5, match_all=False)
    except Exception:
        return None
    hits = [h for h in hits if h["name"] != exclude]
    if not hits:
        return None
    best = max(hits, key=lambda h: h["modified"])
    try:
        md = (notes_dir / best["name"]).read_text(encoding="utf-8")
    except OSError:
        return None
    return {
        "name": best["name"],
        "title": best["title"],
        "modified": best["modified"],
        "tldr": _tldr(md),
    }
