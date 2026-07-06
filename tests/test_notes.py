"""notes.py — task toggling, title extraction, atomic writes."""

from __future__ import annotations

from pathlib import Path

from whisper_to_me import notes

NOTE = """\
# Sprint Planning

## Action Items

- [ ] Ship the exporter — Dana (Friday)
- [x] Book the demo room
- [ ] Update the roadmap

## Transcript

**[0:00:03]** hello there
"""


def _write(tmp_path: Path, content: str = NOTE) -> Path:
    path = tmp_path / "note.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_toggle_task_checks_nth_item(tmp_path):
    path = _write(tmp_path)
    assert notes.toggle_task(path, 0, True)
    text = path.read_text(encoding="utf-8")
    assert "- [x] Ship the exporter — Dana (Friday)" in text
    assert "- [x] Book the demo room" in text  # untouched
    assert "- [ ] Update the roadmap" in text  # untouched


def test_toggle_task_unchecks(tmp_path):
    path = _write(tmp_path)
    assert notes.toggle_task(path, 1, False)
    assert "- [ ] Book the demo room" in path.read_text(encoding="utf-8")


def test_toggle_task_out_of_range(tmp_path):
    path = _write(tmp_path)
    assert not notes.toggle_task(path, 3, True)
    assert path.read_text(encoding="utf-8") == NOTE  # untouched


def test_toggle_task_only_changes_one_line(tmp_path):
    path = _write(tmp_path)
    notes.toggle_task(path, 2, True)
    before, after = NOTE.split("\n"), path.read_text(encoding="utf-8").split("\n")
    diffs = [(a, b) for a, b in zip(before, after) if a != b]
    assert diffs == [("- [ ] Update the roadmap", "- [x] Update the roadmap")]


def test_task_syntax_parity_with_renderer(tmp_path):
    """toggle_task must count exactly what markdown-it-task-lists renders:
    -, *, + and ordered markers count; a missing space after ] does not."""
    content = "\n".join(
        [
            "- [ ] dash",
            "* [ ] star",
            "+ [ ] plus",
            "1. [ ] ordered",
            "- [ ]no-space is not a task",
            "",
        ]
    )
    path = _write(tmp_path, content)
    assert notes.toggle_task(path, 3, True)  # the ordered item is index 3
    assert "1. [x] ordered" in path.read_text(encoding="utf-8")
    assert not notes.toggle_task(path, 4, True)  # no 5th task


def test_note_title_and_fallback(tmp_path):
    assert notes.note_title(_write(tmp_path)) == "Sprint Planning"
    bare = tmp_path / "2026-07-06-bare.md"
    bare.write_text("no heading here\n", encoding="utf-8")
    assert notes.note_title(bare) == "2026-07-06-bare"


def test_write_note_text_replaces_and_cleans_up(tmp_path):
    path = _write(tmp_path)
    notes.write_note_text(path, "# New\n")
    assert path.read_text(encoding="utf-8") == "# New\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_note_writes_frontmatter(tmp_path):
    from datetime import datetime

    path = notes.save_note(
        "Sprint Planning",
        [("0:00:03", "hello there")],
        "## TL;DR\nShort.",
        tmp_path,
        started=datetime(2026, 7, 2, 15, 18),
        attendees=["Dana", "Sam"],
    )
    text = path.read_text(encoding="utf-8")
    fm, body = notes.split_frontmatter(text)
    assert fm == (
        "---\n"
        'title: "Sprint Planning"\n'
        "date: 2026-07-02T15:18\n"
        'attendees: ["Dana", "Sam"]\n'
        "tags: [meeting]\n"
        "source: whisper-to-me\n"
        "---\n"
    )
    assert body.startswith("# Sprint Planning\n")
    assert notes.note_title(path) == "Sprint Planning"  # still read from the H1


def test_frontmatter_escapes_quotes():
    from datetime import datetime

    fm = notes.frontmatter('He said "hi" \\ bye', datetime(2026, 7, 2, 15, 18))
    assert 'title: "He said \\"hi\\" \\\\ bye"' in fm


def test_split_frontmatter_tolerates_absence():
    assert notes.split_frontmatter("# Plain\n") == (None, "# Plain\n")
    assert notes.split_frontmatter("---\nnever closed\n") == (None, "---\nnever closed\n")
