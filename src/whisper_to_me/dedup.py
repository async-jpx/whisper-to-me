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

# The two chunkers gate on the same audio envelope but flush independently,
# so matching chunk boundaries can differ by a couple of seconds.
PAD_SECONDS = 2.0
MIN_RATIO = 0.7        # SequenceMatcher ratio that counts as "same words"
MIN_TOKEN_COVERAGE = 0.75  # or: this share of the You tokens appear in Others
MIN_ECHO_CHARS = 12    # never drop short lines ("yeah", "okay") — too risky


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _overlaps(start_a: datetime, dur_a: float, start_b: datetime, dur_b: float) -> bool:
    a0, a1 = start_a.timestamp(), start_a.timestamp() + dur_a
    b0, b1 = start_b.timestamp() - PAD_SECONDS, start_b.timestamp() + dur_b + PAD_SECONDS
    return a0 < b1 and b0 < a1


def is_echo(you_text: str, others_text: str) -> bool:
    you, others = _norm(you_text), _norm(others_text)
    if len(you) < MIN_ECHO_CHARS:
        return False
    if you in others:
        return True
    if SequenceMatcher(None, you, others).ratio() >= MIN_RATIO:
        return True
    # Partial echo: the mic caught a garbled fragment of a longer utterance.
    you_tokens, others_tokens = you.split(), set(others.split())
    if len(you_tokens) < 4:
        return False
    coverage = sum(t in others_tokens for t in you_tokens) / len(you_tokens)
    return coverage >= MIN_TOKEN_COVERAGE


def matches_any(captured_at: datetime, duration: float, text: str, lines: list[Line]) -> bool:
    """True if (captured_at, duration, text) is an echo of any clean-source line."""
    return any(
        speaker == CLEAN_SPEAKER
        and _overlaps(captured_at, duration, other_at, other_dur)
        and is_echo(text, other_text)
        for other_at, other_dur, speaker, other_text in lines
    )


def drop_echoes(lines: list[Line]) -> list[Line]:
    """Remove mic lines that duplicate a time-overlapping system-audio line."""
    return [
        line
        for line in lines
        if not (line[2] == ECHO_SPEAKER and matches_any(line[0], line[1], line[3], lines))
    ]
