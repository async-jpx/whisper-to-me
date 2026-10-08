"""Incremental live drafts: decode only what is new since the last draft.

The chunker hands the preview worker the *whole* utterance so far every
PREVIEW_INTERVAL_BLOCKS of speech. Decoding all of it each time costs
2 + 4 + … + 30 s of audio per 30 s utterance — more than five times the
meeting's audio through the preview model, and the single largest CPU
consumer during a long monologue. A DraftTracker remembers the text already
decoded for the current utterance and decodes only the tail after it,
committing finished Whisper segments (which end at natural pauses) so the
boundary never splits a word.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE

Segment = tuple[float, float, str]  # (start_s, end_s, text), relative to the audio
Decode = Callable[[np.ndarray], list[Segment]]

# A segment is committed when it ends at least this far before the tail ends:
# Whisper's last segment often runs to the buffer's edge mid-word.
COMMIT_MARGIN_S = 0.3
# Speech without any pause yields one ever-growing segment; past this much
# uncommitted audio the draft is committed whole so each decode stays small.
FORCE_COMMIT_S = 12.0


class DraftTracker:
    """Per-source draft state for the utterance identified by its start time."""

    def __init__(self) -> None:
        self._chunk_id: datetime | None = None
        self._committed_text = ""
        self._committed_samples = 0

    def update(self, chunk_id: datetime, buffer: np.ndarray, decode: Decode) -> str:
        """Return the full draft for `buffer`, decoding only its new tail."""
        if chunk_id != self._chunk_id:
            self._chunk_id = chunk_id
            self._committed_text = ""
            self._committed_samples = 0
        tail = buffer[self._committed_samples :]
        if len(tail) == 0:
            return self._committed_text
        segments = [(s, e, t.strip()) for s, e, t in decode(tail) if t.strip()]
        tail_s = len(tail) / SAMPLE_RATE
        done = [seg for seg in segments if seg[1] <= tail_s - COMMIT_MARGIN_S]
        if tail_s >= FORCE_COMMIT_S:
            done = segments
            if not segments:  # nothing but noise: skip it rather than re-decode it
                self._committed_samples = len(buffer)
        # A commit must consume audio, or the next draft decodes the same
        # tail again and repeats its words.
        if done and (committed_end := min(tail_s, max(seg[1] for seg in done))) > 0:
            self._committed_text = _join(self._committed_text, *(t for _, _, t in done))
            self._committed_samples += int(committed_end * SAMPLE_RATE)
        else:
            done = []
        pending = [seg[2] for seg in segments if seg not in done]
        return _join(self._committed_text, *pending)


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)
