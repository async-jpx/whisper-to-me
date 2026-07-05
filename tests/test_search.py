"""search.py — FTS index sync, ranking plumbing, hostile queries."""

from __future__ import annotations

import os
from pathlib import Path

from whisper_to_me import search


def _note(notes_dir: Path, name: str, title: str, body: str) -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / name
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return path


def test_finds_note_by_body_word(tmp_path):
    _note(tmp_path, "a.md", "Sprint Planning", "We discussed the quarterly budget.")
    _note(tmp_path, "b.md", "Standup", "Nothing about money at all.")
    hits = search.search_notes(tmp_path, "budget")
    assert [h["name"] for h in hits] == ["a.md"]
    assert hits[0]["title"] == "Sprint Planning"
    assert search.HL_OPEN + "budget" + search.HL_CLOSE in hits[0]["snippet"]
    assert hits[0]["modified"]


def test_prefix_match_while_typing(tmp_path):
    _note(tmp_path, "a.md", "Planning", "quarterly budget review")
    assert search.search_notes(tmp_path, "budg")
    assert search.search_notes(tmp_path, "quarterly budg")


def test_diacritics_fold(tmp_path):
    _note(tmp_path, "a.md", "Café sync", "the café menu came up")
    assert search.search_notes(tmp_path, "cafe")


def test_hostile_queries_never_raise(tmp_path):
    _note(tmp_path, "a.md", "Planning", "budget things")
    for q in ['"unbalanced', "NEAR(", "a AND OR NOT", "(((", '"" "" *', "\U0001F389", "-", ""]:
        assert isinstance(search.search_notes(tmp_path, q), list)


def test_sync_picks_up_edits_and_deletions(tmp_path):
    path = _note(tmp_path, "a.md", "Planning", "original topic alpha")
    assert search.search_notes(tmp_path, "alpha")

    path.write_text("# Planning\n\nnow about bravo\n", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))
    assert not search.search_notes(tmp_path, "alpha")
    assert search.search_notes(tmp_path, "bravo")

    path.unlink()
    assert not search.search_notes(tmp_path, "bravo")


def test_empty_query_and_missing_dir(tmp_path):
    assert search.search_notes(tmp_path / "nope", "x") == []
    _note(tmp_path, "a.md", "T", "body")
    assert search.search_notes(tmp_path, "   ") == []


def test_index_file_stays_out_of_the_corpus(tmp_path):
    _note(tmp_path, "a.md", "T", "wtm index test body")
    search.search_notes(tmp_path, "body")
    assert (tmp_path / search.INDEX_FILENAME).exists()
    hits = search.search_notes(tmp_path, "body")
    assert [h["name"] for h in hits] == ["a.md"]
