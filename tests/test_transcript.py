"""notes.parse_transcript / frontmatter_fields — the one server-side parser
behind GET /api/notes/{name}/transcript and NoteMeta."""

from __future__ import annotations

from datetime import datetime

from whisper_to_me import notes
from whisper_to_me.notes import TranscriptLine

NOTE = """\
---
title: "Q3 \\"plan\\" review"
date: 2026-10-04T14:30
app: "Zoom"
tags: [meeting]
source: whisper-to-me
---
# Q3 plan review

## Action Items

- [ ] **[0:09:09]** not a transcript line

## Transcript

**[0:00:03]** **You:** Hello there.
**[0:01:15]** **Speaker A:** We ship **Friday**.
**[1:02:09]** No label here.
*(stray italics)*
"""


def test_parses_only_the_transcript_section():
    assert notes.parse_transcript(NOTE) == [
        TranscriptLine(t=3, stamp="0:00:03", speaker="You", text="Hello there."),
        TranscriptLine(t=75, stamp="0:01:15", speaker="Speaker A", text="We ship **Friday**."),
        TranscriptLine(t=3729, stamp="1:02:09", speaker=None, text="No label here."),
    ]


def test_transcript_ends_at_the_next_section():
    text = "## Transcript\n\n**[0:00:01]** a\n\n## Later\n\n**[0:00:02]** b\n"
    assert [line.text for line in notes.parse_transcript(text)] == ["a"]


def test_no_transcript_section_is_empty():
    assert notes.parse_transcript("# Just notes\n\n**[0:00:01]** hi\n") == []


def test_round_trips_what_save_note_writes(tmp_path):
    path = notes.save_note(
        "T", [("0:00:04", "**You:** hi"), ("0:00:09", "**Others:** yo")], None,
        tmp_path, datetime(2026, 10, 4, 9, 0), app='Weird "App"',
    )
    text = path.read_text()
    assert [(line.t, line.speaker, line.text) for line in notes.parse_transcript(text)] == [
        (4, "You", "hi"), (9, "Others", "yo"),
    ]
    assert notes.frontmatter_fields(text)["app"] == 'Weird "App"'
    assert notes.meeting_date(text) == "2026-10-04T09:00:00"


def test_live_journal_lines_parse(tmp_path):
    path = notes.start_live_note("Live", datetime(2026, 10, 4, 9, 0), tmp_path)
    notes.append_line(path, "0:00:02", "**You:** first")
    assert notes.parse_transcript(path.read_text()) == [
        TranscriptLine(t=2, stamp="0:00:02", speaker="You", text="first")
    ]


def test_frontmatter_fields_unquote_and_ignore_missing():
    fields = notes.frontmatter_fields(NOTE)
    assert fields["title"] == 'Q3 "plan" review'
    assert fields["app"] == "Zoom"
    assert notes.meeting_date(NOTE) == "2026-10-04T14:30:00"
    assert notes.frontmatter_fields("# no frontmatter") == {}
    assert notes.meeting_date("---\ndate: garbage\n---\n") is None
