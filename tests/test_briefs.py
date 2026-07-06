"""Briefs (Phase 4.5): TL;DR extraction and the recency/exclude pick over the
FTS index. No network, no Ollama."""

from __future__ import annotations

import os
import time

import whisper_to_me.briefs as briefs

NOTE_WITH_TLDR = (
    "---\ntitle: X\n---\n# X\n\n## TL;DR\nWe decided to ship.\nIt was good.\n\n"
    "## Decisions\n- a\n"
)


def test_tldr_extracts_and_collapses():
    assert briefs._tldr(NOTE_WITH_TLDR) == "We decided to ship. It was good."


def test_tldr_absent_falls_back_to_first_paragraph():
    md = "# Title\n\n*Recorded Monday — whisper-to-me*\n\nSome opening paragraph.\n\n## Transcript\n"
    assert briefs._tldr(md) == "Some opening paragraph."


def test_tldr_empty_note():
    assert briefs._tldr("# Only a title\n") == ""


def _note(d, name, title, tldr):
    (d / name).write_text(
        f"---\ntitle: {title}\n---\n# {title}\n\n## TL;DR\n{tldr}\n", encoding="utf-8"
    )


def test_find_brief_picks_most_recent_match(tmp_path):
    _note(tmp_path, "a.md", "Roadmap Sync", "Old one.")
    _note(tmp_path, "b.md", "Roadmap Sync", "New one.")
    old = time.time() - 10_000
    os.utime(tmp_path / "a.md", (old, old))
    brief = briefs.find_brief(tmp_path, "Roadmap Sync")
    assert brief["name"] == "b.md"
    assert brief["tldr"] == "New one."


def test_find_brief_honors_exclude(tmp_path):
    _note(tmp_path, "a.md", "Roadmap Sync", "Only one.")
    assert briefs.find_brief(tmp_path, "Roadmap Sync", exclude="a.md") is None


def test_find_brief_no_match(tmp_path):
    _note(tmp_path, "a.md", "Weather Chat", "rain.")
    assert briefs.find_brief(tmp_path, "Quarterly Budget Review") is None


def test_find_brief_empty_title(tmp_path):
    assert briefs.find_brief(tmp_path, "") is None
