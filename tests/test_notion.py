"""notion_export.py — markdown→blocks conversion and the push flow.

Everything here runs offline: the push test fakes `_request`, asserting on
exactly what would be sent — there is no network in the test suite, ever.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from whisper_to_me import notes, notion_export
from whisper_to_me.notion_export import (
    MAX_CHILDREN,
    MAX_TEXT,
    NotionError,
    markdown_to_blocks,
    preview_push,
    push_note,
)

NOTE_MD = (
    notes.frontmatter("Sprint Planning", datetime(2026, 7, 2, 15, 18), ["Dana", "Sam"])
    + """\
# Sprint Planning

*Recorded Thursday*

## Action Items

- [ ] Ship the exporter
- [x] Book the room

## Discussion

**Roadmap**
- point one
1. first
> a quote

---

## Transcript

**[0:00:03]** **You:** hello
"""
)


def test_markdown_to_blocks_shapes():
    blocks = markdown_to_blocks(NOTE_MD)
    types = [b["type"] for b in blocks]
    # First H1 becomes the page title, not a block.
    assert types == [
        "paragraph",       # *Recorded Thursday*
        "heading_2",       # Action Items
        "to_do",
        "to_do",
        "heading_2",       # Discussion
        "paragraph",       # **Roadmap**
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "divider",
        "heading_2",       # Transcript
        "paragraph",
    ]
    todo_open, todo_done = blocks[2], blocks[3]
    assert todo_open["to_do"]["checked"] is False
    assert todo_done["to_do"]["checked"] is True
    assert todo_done["to_do"]["rich_text"][0]["text"]["content"] == "Book the room"


def test_bold_and_code_spans():
    (block,) = markdown_to_blocks("plain **bold** and `code` end")
    spans = block["paragraph"]["rich_text"]
    assert [s["text"]["content"] for s in spans] == ["plain ", "bold", " and ", "code", " end"]
    assert spans[1]["annotations"] == {"bold": True}
    assert spans[3]["annotations"] == {"code": True}
    assert "annotations" not in spans[0]


def test_heading_levels_clamped():
    blocks = markdown_to_blocks("## two\n### three\n#### four\n")
    assert [b["type"] for b in blocks] == ["heading_2", "heading_3", "heading_3"]


def test_long_text_chunked_under_notion_limit():
    (block,) = markdown_to_blocks("x" * (MAX_TEXT * 2 + 5))
    spans = block["paragraph"]["rich_text"]
    assert [len(s["text"]["content"]) for s in spans] == [MAX_TEXT, MAX_TEXT, 5]


def test_preview_reports_what_would_be_sent(tmp_path):
    path = tmp_path / "2026-07-02-1518-sprint-planning.md"
    path.write_text(NOTE_MD, encoding="utf-8")
    preview = preview_push(path, "db123")
    assert preview.title == "Sprint Planning"
    assert preview.date == "2026-07-02T15:18:00"
    assert preview.attendees == ["Dana", "Sam"]
    assert preview.block_count == len(markdown_to_blocks(NOTE_MD))
    assert preview.database_id == "db123"


@pytest.fixture()
def fake_notion(monkeypatch):
    """Capture every would-be API call; serve a database schema for the GET."""
    calls: list[tuple[str, str, dict | None]] = []

    def _fake_request(method, url, token, payload=None):
        calls.append((method, url, payload))
        if method == "GET":
            return {
                "properties": {
                    "Name": {"type": "title"},
                    "When": {"type": "date"},
                    "Attendees": {"type": "multi_select"},
                }
            }
        return {"id": "page1", "url": "https://www.notion.so/page1"}

    monkeypatch.setattr(notion_export, "_request", _fake_request)
    return calls


def test_push_note_properties_and_children(tmp_path, fake_notion):
    path = tmp_path / "2026-07-02-1518-sprint-planning.md"
    path.write_text(NOTE_MD, encoding="utf-8")

    url = push_note(path, "tok", "db123")
    assert url == "https://www.notion.so/page1"

    methods = [c[0] for c in fake_notion]
    assert methods == ["GET", "POST"]
    _, _, payload = fake_notion[1]
    assert payload["parent"] == {"database_id": "db123"}
    props = payload["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == "Sprint Planning"
    assert props["When"]["date"]["start"] == "2026-07-02T15:18:00"
    assert [o["name"] for o in props["Attendees"]["multi_select"]] == ["Dana", "Sam"]
    assert len(payload["children"]) == len(markdown_to_blocks(NOTE_MD))


def test_push_note_batches_past_100_blocks(tmp_path, fake_notion):
    lines = "\n\n".join(f"line {i}" for i in range(MAX_CHILDREN + 30))
    path = tmp_path / "big.md"
    path.write_text(f"# Big\n\n{lines}\n", encoding="utf-8")

    push_note(path, "tok", "db123")
    methods = [c[0] for c in fake_notion]
    assert methods == ["GET", "POST", "PATCH"]
    assert len(fake_notion[1][2]["children"]) == MAX_CHILDREN
    assert len(fake_notion[2][2]["children"]) == 30
    assert "blocks/page1/children" in fake_notion[2][1]


def test_push_note_requires_title_property(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("# T\n", encoding="utf-8")
    monkeypatch.setattr(
        notion_export, "_request", lambda *a, **k: {"properties": {"When": {"type": "date"}}}
    )
    with pytest.raises(NotionError, match="title property"):
        push_note(path, "tok", "db123")
