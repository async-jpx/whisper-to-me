"""export.py — frontmatter retrofit and vault copies (all local files)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from whisper_to_me import notes
from whisper_to_me.export import copy_to_vault, export_obsidian, note_date, vault_ready_text

LEGACY = """\
# Sprint Planning

*Recorded Thursday 02 July 2026, 15:18 — whisper-to-me*

## Transcript

**[0:00:03]** hello there
"""


def _legacy_note(tmp_path: Path) -> Path:
    path = tmp_path / "2026-07-02-1518-sprint-planning.md"
    path.write_text(LEGACY, encoding="utf-8")
    return path


def test_note_date_from_filename(tmp_path):
    assert note_date(_legacy_note(tmp_path)) == datetime(2026, 7, 2, 15, 18)


def test_note_date_falls_back_to_mtime(tmp_path):
    path = tmp_path / "not-a-dated-name.md"
    path.write_text(LEGACY, encoding="utf-8")
    assert note_date(path) == datetime.fromtimestamp(path.stat().st_mtime)


def test_vault_ready_text_adds_frontmatter_once(tmp_path):
    path = _legacy_note(tmp_path)
    text = vault_ready_text(path)
    assert text.startswith('---\ntitle: "Sprint Planning"\ndate: 2026-07-02T15:18\n')
    assert "tags: [meeting]" in text
    assert text.endswith(LEGACY)  # body untouched

    path.write_text(text, encoding="utf-8")
    assert vault_ready_text(path) == text  # idempotent


def test_vault_ready_text_keeps_fresh_notes_as_is(tmp_path):
    path = tmp_path / "2026-07-02-1518-fresh.md"
    fresh = notes.frontmatter("Fresh", datetime(2026, 7, 2, 15, 18)) + "# Fresh\n"
    path.write_text(fresh, encoding="utf-8")
    assert vault_ready_text(path) == fresh


def test_copy_to_vault_never_clobbers_without_overwrite(tmp_path):
    src = _legacy_note(tmp_path)
    vault = tmp_path / "vault"

    dest = copy_to_vault(src, vault)
    assert dest == vault / src.name and dest.is_file()

    (vault / src.name).write_text("edited in Obsidian", encoding="utf-8")
    assert copy_to_vault(src, vault) is None
    assert (vault / src.name).read_text(encoding="utf-8") == "edited in Obsidian"

    assert copy_to_vault(src, vault, overwrite=True) == dest
    assert (vault / src.name).read_text(encoding="utf-8").startswith("---\n")


def test_export_obsidian_back_catalog(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    _legacy_note(notes_dir)
    (notes_dir / "2026-07-03-0900-other.md").write_text("# Other\n", encoding="utf-8")
    (notes_dir / ".wtm-index.sqlite3").write_text("", encoding="utf-8")  # not a note

    vault = tmp_path / "vault"
    copied, skipped = export_obsidian(notes_dir, vault)
    assert len(copied) == 2 and skipped == []
    assert sorted(p.name for p in vault.glob("*")) == sorted(copied)

    copied, skipped = export_obsidian(notes_dir, vault)  # second run: all skipped
    assert copied == [] and len(skipped) == 2
