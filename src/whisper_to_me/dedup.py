"""Cross-source echo removal.

When remote voices play through the speakers, the microphone hears them too,
so the same words arrive twice: cleanly on the system-audio source ("Others")
and garbled on the mic source ("You"). A "You" line whose audio interval
overlaps an "Others" line and whose text is close enough to it is an echo and
is dropped. This is a text-level safety net; acoustic echo cancellation on the
mic signal is the eventual root fix.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

# Source names as used by session.py. The mic is the contaminated source;
# the system tap only ever carries app audio, so it needs no filtering.
ECHO_SPEAKER = "You"
CLEAN_SPEAKER = "Others"

# A line: (captured_at, duration_seconds, speaker, text).
Line = tuple[datetime, float, str, str]

# An echo starts almost simultaneously with its source (speaker→mic path is
# ~0.1-0.3 s); onset-aligned pairs get the loose text match. A mic segment
# merely *inside* the source interval could just be a genuine quick reply, so
# it must match the text near-exactly to count as a (partial) echo.
ONSET_TOLERANCE = 1.5   # |Δstart| for the aligned case, incl. chunker jitter
INSIDE_MARGIN = 1.0     # must start at least this far before the source ends
MIN_RATIO = 0.7         # aligned case: SequenceMatcher ratio for "same words"
STRICT_RATIO = 0.85     # inside case: near-exact only
MIN_TOKEN_COVERAGE = 0.75  # aligned case: share of You tokens found in Others
MIN_ECHO_CHARS = 12     # never drop short lines ("yeah", "okay") — too risky


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def is_echo(you_text: str, others_text: str, strict: bool = False) -> bool:
    you, others = _norm(you_text), _norm(others_text)
    if len(you) < MIN_ECHO_CHARS:
        return False
    if you in others:
        return True
    if SequenceMatcher(None, you, others).ratio() >= (STRICT_RATIO if strict else MIN_RATIO):
        return True
    if strict:
        return False
    # Garbled echo: most of the You words appear somewhere in the source line.
    you_tokens, others_tokens = you.split(), set(others.split())
    if len(you_tokens) < 4:
        return False
    coverage = sum(t in others_tokens for t in you_tokens) / len(you_tokens)
    return coverage >= MIN_TOKEN_COVERAGE


def _is_echo_of(
    at: datetime, duration: float, text: str,
    other_at: datetime, other_dur: float, other_text: str,
) -> bool:
    delta_start = (at - other_at).total_seconds()
    if abs(delta_start) <= ONSET_TOLERANCE:
        return is_echo(text, other_text)
    # Started mid-source: could be a fragment of a long utterance the mic
    # chunked differently — or a genuine reply. Only near-exact text counts.
    ends_at = other_at.timestamp() + other_dur
    if 0 < delta_start and at.timestamp() <= ends_at - INSIDE_MARGIN:
        return is_echo(text, other_text, strict=True)
    return False


def matches_any(captured_at: datetime, duration: float, text: str, lines: list[Line]) -> bool:
    """True if (captured_at, duration, text) is an echo of any clean-source line."""
    return any(
        speaker == CLEAN_SPEAKER
        and _is_echo_of(captured_at, duration, text, other_at, other_dur, other_text)
        for other_at, other_dur, speaker, other_text in lines
    )


def drop_echoes(lines: list[Line]) -> list[Line]:
    """Remove mic lines that duplicate a time-overlapping system-audio line."""
    return [
        line
        for line in lines
        if not (line[2] == ECHO_SPEAKER and matches_any(line[0], line[1], line[3], lines))
    ]
