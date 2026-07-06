"""Notes-first enhancement (Phase 4.2): the scratchpad must reach both the
extraction and synthesis prompts, and empty notes must reproduce today's
prompts byte-for-byte. Ollama is always mocked."""

from __future__ import annotations

import whisper_to_me.summarize as summ


class _Capture:
    """Records every _chat / _chat_json call so we can assert on the prompts
    without touching Ollama."""

    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, str]] = []  # (system, user)
        self.json_calls: list[tuple[str, str]] = []

    def chat(self, model, system, user, timeout=600, schema=None):
        self.chat_calls.append((system, user))
        return "## TL;DR\nnotes\n"

    def chat_json(self, model, system, user, schema):
        self.json_calls.append((system, user))
        if schema is summ.TITLE_SCHEMA:
            return {"title": "A Title"}
        return {k: [] for k in summ.LIST_KEYS} | {"purpose": "", "action_items": []}


def _install(monkeypatch) -> _Capture:
    cap = _Capture()
    monkeypatch.setattr(summ, "_chat", cap.chat)
    monkeypatch.setattr(summ, "_chat_json", cap.chat_json)
    return cap


TRANSCRIPT = "**You:** we should ship the exporter\n**Others:** agreed, by Friday\n"


def test_synth_system_empty_notes_is_byte_for_byte_original():
    # The refactor into HEADER/SECTIONS/RULES must not change the default
    # prompt at all — this is the regression guard the plan calls for.
    expected = (
        summ.SYNTH_HEADER
        + "\n\n"
        + summ.SYNTH_SECTIONS
        + "\n\n"
        + summ.SYNTH_RULES
        + "\n"
    )
    assert summ._synth_system("") == expected
    assert "Your Notes, Expanded" not in summ._synth_system("")


def test_user_notes_reach_synthesis_and_extraction(monkeypatch):
    cap = _install(monkeypatch)
    summ.summarize_meeting(TRANSCRIPT, user_notes="- decide on the launch date")

    synth_system, synth_user = cap.chat_calls[-1]
    assert "Your Notes, Expanded" in synth_system
    assert "decide on the launch date" in synth_user
    assert "Notes typed by the note-taker" in synth_user

    extract_user = cap.json_calls[0][1]
    assert "decide on the launch date" in extract_user
    assert "note-taker's own notes" in extract_user


def test_empty_notes_omit_the_extra_section(monkeypatch):
    cap = _install(monkeypatch)
    summ.summarize_meeting(TRANSCRIPT)
    synth_system, synth_user = cap.chat_calls[-1]
    assert "Your Notes, Expanded" not in synth_system
    assert "Notes typed by the note-taker" not in synth_user
    assert "note-taker's own notes" not in cap.json_calls[0][1]


def test_user_notes_truncated_in_extraction_prefix(monkeypatch):
    cap = _install(monkeypatch)
    huge = "x" * 5000
    summ.summarize_meeting(TRANSCRIPT, user_notes=huge)
    extract_user = cap.json_calls[0][1]
    # Extraction prefix caps notes at 2000 chars (it repeats per window).
    assert "x" * 2000 in extract_user
    assert "x" * 2001 not in extract_user
