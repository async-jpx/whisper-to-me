"""Deterministic per-meeting speaking stats and the cross-meeting progress view.

Nothing here calls a model. Transcript stats come from the text, and voice
stats come from the user's own audio when it was kept (coaching/voice.py,
cached per note). The dashboard therefore covers every note, analyzed or not.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import notes
from . import store, voice

FILLER_RE = re.compile(
    r"\b(?:u+m+|u+h+|e+r+m+|hmm+|you know|i mean|kind of|sort of|basically)\b",
    re.IGNORECASE,
)
RECENT = 5
MIN_WORDS = 20  # fewer words than this says nothing about how you speak


def _is_you(speaker: str | None) -> bool:
    return speaker in (None, "You")


def meeting_stats(lines: list[notes.TranscriptLine]) -> dict | None:
    """Speaking stats for the user's lines, None when the user never spoke."""
    you = [line for line in lines if _is_you(line.speaker)]
    if not you:
        return None
    you_words = sum(len(line.text.split()) for line in you)
    if you_words < MIN_WORDS:
        return None
    labeled = any(line.speaker is not None for line in lines)
    all_words = sum(len(line.text.split()) for line in lines)
    turns: list[int] = []
    previous_you = False
    for line in lines:
        if _is_you(line.speaker):
            words = len(line.text.split())
            if previous_you:
                turns[-1] += words
            else:
                turns.append(words)
        previous_you = _is_you(line.speaker)
    fillers = sum(len(FILLER_RE.findall(line.text)) for line in you)
    return {
        "your_words": you_words,
        "talk_share": round(you_words / all_words, 3) if labeled and all_words else None,
        "turns": len(turns),
        "avg_turn_words": round(you_words / len(turns), 1),
        "longest_turn_words": max(turns),
        "questions": sum(line.text.count("?") for line in you),
        "fillers_per_100_words": round(fillers * 100 / you_words, 2),
    }


def _started(path: Path, text: str) -> str:
    """Frontmatter date, else note_path's `YYYY-MM-DD-HHMM-` prefix, else mtime."""
    if date := notes.meeting_date(text):
        return date
    try:
        return datetime.strptime(path.name[:15], "%Y-%m-%d-%H%M").isoformat()
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()


VOICE_METRICS = (
    "speaking_rate_wpm", "pauses_per_minute", "pitch_range_semitones", "loudness_range_db",
)
METRICS = (
    "talk_share", "avg_turn_words", "longest_turn_words",
    "questions", "fillers_per_100_words", *VOICE_METRICS,
)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def dashboard(notes_dir: Path) -> dict:
    """Every meeting the user spoke in, oldest first, plus recent-vs-earlier
    averages and the practice focus from each saved meeting review."""
    meetings = []
    for path in notes_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        stats = meeting_stats(notes.parse_transcript(text))
        if stats is None:
            continue
        measured = voice.voice_metrics(path) or {}
        stats |= {key: measured.get(key) for key in VOICE_METRICS}
        review = (store.load(notes_dir, path.name) or {}).get("review") or {}
        meetings.append({
            "name": path.name,
            "title": notes.note_title(path),
            "started": _started(path, text),
            "stats": stats,
            "focus": (review.get("next_time") or {}).get("practice") or None,
            "patterns": [p.get("pattern", "") for p in review.get("patterns", [])],
        })
    meetings.sort(key=lambda m: m["started"])
    recent, earlier = meetings[-RECENT:], meetings[-2 * RECENT:-RECENT]
    trends = {
        metric: {
            "recent": _mean([m["stats"][metric] for m in recent if m["stats"][metric] is not None]),
            "earlier": _mean([m["stats"][metric] for m in earlier if m["stats"][metric] is not None]),
        }
        for metric in METRICS
    }
    return {"meetings": meetings, "trends": trends, "window": RECENT}
