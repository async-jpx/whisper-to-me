"""Follow-up drafts (Phase 4.6): the prompt must exclude the transcript and
frontmatter (summary only), clamp size, and round-trip through _chat. Ollama is
mocked."""

from __future__ import annotations

import whisper_to_me.followup as followup
import whisper_to_me.summarize as summ

NOTE = (
    '---\ntitle: Sprint\nattendees: ["Priya"]\n---\n# Sprint\n\n## TL;DR\nWe shipped.\n\n'
    "## Action Items\n- [ ] do the thing — Priya\n\n"
    "## Transcript\n\n**[0:00:01]** secret transcript words\n"
)


def test_draft_excludes_transcript_and_frontmatter(monkeypatch):
    captured = {}

    def chat(m, s, u, timeout=600, schema=None):
        captured["system"], captured["user"] = s, u
        return "Subject: Recap\n\nThanks all."

    monkeypatch.setattr(summ, "_chat", chat)
    out = followup.draft_followup(NOTE)
    assert out.startswith("Subject:")
    assert "secret transcript words" not in captured["user"]  # transcript dropped
    assert "title: Sprint" not in captured["user"]  # frontmatter dropped
    assert "We shipped." in captured["user"]  # summary kept
    assert "do the thing" in captured["user"]  # action items kept


def test_draft_clamps_to_max_chars(monkeypatch):
    monkeypatch.setattr(summ, "_chat", lambda m, s, u, timeout=600, schema=None: u)
    big = "# T\n\n## TL;DR\n" + ("x" * 30_000) + "\n\n## Transcript\n\nyyy\n"
    assert len(followup.draft_followup(big)) <= followup.MAX_CHARS
