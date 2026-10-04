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
        assert isinstance(search.search(tmp_path, q), list)


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


MEETING = """---
title: "Exporter sync"
date: 2026-09-30T10:00
app: "Zoom"
tags: [meeting]
---
# Exporter sync

*Recorded Tuesday with Hush*

## TL;DR
We agreed the release ships soon.

---

## Transcript

**[0:00:03]** **You:** Morning everyone.
**[0:03:12]** **Others:** The café exporter ships on Friday, Dana owns it.
**[1:05:40]** **You:** Great, Friday it is.
"""


def _meeting(notes_dir: Path, name: str = "m.md", text: str = MEETING) -> Path:
    path = notes_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_line_hit_carries_seconds_and_speaker(tmp_path):
    _meeting(tmp_path)
    [result] = search.search(tmp_path, "exporter")
    assert (result["name"], result["title"]) == ("m.md", "Exporter sync")
    assert (result["date"], result["app"]) == ("2026-09-30T10:00:00", "Zoom")
    lines = [h for h in result["hits"] if h["kind"] == "line"]
    assert [(h["t"], h["speaker"]) for h in lines] == [(192, "Others")]
    assert search.HL_OPEN + "exporter" + search.HL_CLOSE in lines[0]["snippet"]
    assert "**" not in lines[0]["snippet"] and "0:03:12" not in lines[0]["snippet"]


def test_hours_in_stamp_and_hit_cap(tmp_path):
    path = _meeting(tmp_path)
    [result] = search.search(tmp_path, "friday")
    assert [h["t"] for h in result["hits"]] == [192, 3940]
    extra = "".join(f"**[0:10:{i:02d}]** friday again {i}\n" for i in range(10))
    path.write_text(MEETING + extra, encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))
    [result] = search.search(tmp_path, "friday")
    assert len(result["hits"]) == search.HITS_PER_NOTE


def test_title_and_summary_hits(tmp_path):
    _meeting(tmp_path)
    [result] = search.search(tmp_path, "sync")
    assert [h["kind"] for h in result["hits"]] == ["title"]
    [result] = search.search(tmp_path, "release")
    assert [(h["kind"], h["t"]) for h in result["hits"]] == [("summary", None)]


def test_summary_snippet_drops_list_and_task_markers(tmp_path):
    (tmp_path / "t.md").write_text(
        "# Tasks\n\n## Action Items\n\n- [ ] Update the changelog\n- [x] Ship it\n",
        encoding="utf-8",
    )
    [result] = search.search(tmp_path, "changelog")
    [hit] = result["hits"]
    assert "[ ]" not in hit["snippet"] and "[x]" not in hit["snippet"]
    assert "- " not in hit["snippet"]


def test_prefix_on_every_long_term_and_last(tmp_path):
    _meeting(tmp_path)
    assert search.search(tmp_path, "expo fri")  # both still being typed
    assert search.search(tmp_path, "caf")  # diacritics folded, prefix
    assert not search.search(tmp_path, "ex ships")  # 2-char non-last is exact


def test_and_across_lines_or_for_chat(tmp_path):
    _meeting(tmp_path)
    # AND is note-wide: "dana" and "morning" sit on different lines.
    [result] = search.search(tmp_path, "dana morning")
    assert {h["t"] for h in result["hits"]} == {3, 192}
    assert search.search(tmp_path, "dana zebra") == []
    or_hits = search.search_notes(tmp_path, "dana zebra", match_all=False)
    assert [h["name"] for h in or_hits] == ["m.md"]


def _dated(notes_dir: Path, name: str, date: str, title: str, body: str) -> None:
    (notes_dir / name).write_text(
        f"---\ndate: {date}\n---\n# {title}\n\n## Transcript\n\n**[0:00:01]** {body}\n",
        encoding="utf-8",
    )


def test_ranking_recency_breaks_ties_title_beats_age(tmp_path):
    _dated(tmp_path, "old.md", "2025-01-01T10:00", "Weekly", "budget talk")
    _dated(tmp_path, "new.md", "2026-09-01T10:00", "Weekly", "budget talk")
    _dated(tmp_path, "other.md", "2026-09-01T10:00", "Other", "nothing here")
    _dated(tmp_path, "filler.md", "2026-09-01T10:00", "Filler", "more nothing")
    assert [r["name"] for r in search.search(tmp_path, "budget")] == ["new.md", "old.md"]
    _dated(tmp_path, "title.md", "2024-01-01T10:00", "Budget review", "budget numbers")
    assert search.search(tmp_path, "budget")[0]["name"] == "title.md"


def test_schema_version_rebuilds_old_index(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / search.INDEX_FILENAME)
    db.executescript(
        "CREATE TABLE files (name TEXT PRIMARY KEY, mtime REAL NOT NULL);"
        "CREATE VIRTUAL TABLE notes_fts USING fts5(name UNINDEXED, title, body);"
    )
    db.execute("INSERT INTO files VALUES ('m.md', 0)")
    db.commit()
    db.close()
    _meeting(tmp_path)
    [result] = search.search(tmp_path, "exporter")
    assert [(h["kind"], h["t"]) for h in result["hits"]] == [("title", None), ("line", 192)]
    db = sqlite3.connect(tmp_path / search.INDEX_FILENAME)
    assert db.execute("PRAGMA user_version").fetchone()[0] == search.SCHEMA_VERSION
    db.close()


def test_no_duplicate_hits_after_resync(tmp_path):
    path = _meeting(tmp_path)
    search.search(tmp_path, "exporter")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))
    [result] = search.search(tmp_path, "exporter")
    assert [(h["kind"], h["t"]) for h in result["hits"]] == [("title", None), ("line", 192)]
