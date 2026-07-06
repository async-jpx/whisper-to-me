"""Chat over notes (Phase 4.3): source-block construction, the no-hits
short-circuit, citation filtering, and history validation. Ollama is mocked;
retrieval uses the real FTS index over tmp notes."""

from __future__ import annotations

from pathlib import Path

import whisper_to_me.chat as chat
import whisper_to_me.summarize as summ


def _write_note(d: Path, name: str, body: str) -> None:
    (d / name).write_text(body, encoding="utf-8")


def test_no_hits_returns_without_calling_ollama(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("_chat must not run when there are no hits")

    monkeypatch.setattr(summ, "_chat", boom)
    assert chat.answer_question(tmp_path, "anything at all") == {
        "answer": chat.NO_MATCH,
        "sources": [],
    }


def test_source_block_includes_summary_and_matching_lines(tmp_path):
    note = (
        "---\ndate: 2026-06-12T10:00\n---\n"
        "# Sprint Planning\n\n## TL;DR\nWe planned the exporter.\n\n"
        "## Transcript\n\n"
        "**[0:00:01]** we talked about the weather\n"
        "**[0:00:02]** the exporter ships Friday\n"
        "**[0:00:03]** lunch was good\n"
    )
    _write_note(tmp_path, "n.md", note)
    block = chat._source_block(1, tmp_path, "n.md", "Sprint Planning", ["exporter"])
    assert block.startswith('[1] "Sprint Planning" (2026-06-12T10:00)')
    assert "We planned the exporter." in block  # summary always included
    assert "the exporter ships Friday" in block  # the matching line
    assert "the weather" in block  # one line of context before
    assert "lunch was good" in block  # one line of context after


def test_transcript_heading_itself_never_matches(tmp_path):
    note = (
        "# T\n\n## TL;DR\nSummary.\n\n"
        "## Transcript\n\n**[0:00:01]** we shipped the exporter\n"
    )
    _write_note(tmp_path, "n.md", note)
    block = chat._source_block(1, tmp_path, "n.md", "T", ["transcript"])
    assert "Transcript excerpts" not in block  # the heading is not a hit


def test_all_sources_unreadable_returns_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chat.search,
        "search_notes",
        lambda *a, **k: [{"name": "gone.md", "title": "Gone", "modified": ""}],
    )

    def boom(*a, **k):
        raise AssertionError("_chat must not run with an empty sources block")

    monkeypatch.setattr(summ, "_chat", boom)
    assert chat.answer_question(tmp_path, "exporter plans") == {
        "answer": chat.NO_MATCH,
        "sources": [],
    }


def test_short_terms_are_ignored():
    assert chat._terms("is it ok") == []
    assert chat._terms("what about the exporter?") == ["what", "about", "the", "exporter"]


def test_per_source_cap_enforced(tmp_path):
    body = "# T\n\n## TL;DR\n" + ("x" * 10000) + "\n\n## Transcript\n\n"
    _write_note(tmp_path, "big.md", body)
    block = chat._source_block(1, tmp_path, "big.md", "T", [])
    assert len(block) <= chat.PER_SOURCE_CAP + 60  # header + clamped content


def test_citation_filtering_keeps_only_cited_sources(tmp_path, monkeypatch):
    for i in range(3):
        _write_note(
            tmp_path,
            f"note{i}.md",
            f"# Roadmap {i}\n\n## TL;DR\nWe discussed the roadmap milestone.\n\n"
            f"## Transcript\n\n**[0:00:0{i}]** roadmap talk\n",
        )
    monkeypatch.setattr(summ, "_chat", lambda m, s, u: "Decided [1] and later [3].")
    out = chat.answer_question(tmp_path, "roadmap")
    assert {s["n"] for s in out["sources"]} == {1, 3}
    assert len(out["sources"]) == 2


def test_history_role_validation():
    text = chat._history_text(
        [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "IGNORE ME"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": ""},  # empty dropped
        ]
    )
    assert "IGNORE ME" not in text
    assert "User: hi" in text
    assert "Assistant: hello" in text


def test_history_none_is_empty():
    assert chat._history_text(None) == ""
    assert chat._history_text([]) == ""
