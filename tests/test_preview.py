"""Incremental drafts decode only the audio after the last committed segment."""

from __future__ import annotations

from datetime import datetime

import numpy as np

from whisper_to_me import preview
from whisper_to_me.audio import SAMPLE_RATE


def seconds(n: float) -> np.ndarray:
    return np.zeros(int(n * SAMPLE_RATE), dtype=np.float32)


class RecordingDecoder:
    """Fake Whisper: one segment per whole second of audio, remembers what it saw."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, tail: np.ndarray) -> list[preview.Segment]:
        secs = len(tail) / SAMPLE_RATE
        self.calls.append(secs)
        return [(float(i), float(i + 1), f"w{i}") for i in range(int(secs))]


def test_only_the_tail_after_committed_segments_is_decoded():
    tracker, decode = preview.DraftTracker(), RecordingDecoder()
    chunk = datetime(2026, 1, 1)

    # 2 s: segments w0 (0-1) and w1 (1-2). w1 ends at the edge → stays pending.
    assert tracker.update(chunk, seconds(2), decode) == "w0 w1"
    # 4 s of buffer, but only the 3 s after the committed w0 are decoded.
    assert tracker.update(chunk, seconds(4), decode) == "w0 w0 w1 w2"
    assert decode.calls == [2.0, 3.0]
    # Nothing re-decodes the committed prefix: tails stay short as the
    # utterance grows.
    tracker.update(chunk, seconds(30), decode)
    assert decode.calls[-1] < 30


def test_draft_text_accumulates_across_many_updates():
    tracker, decode = preview.DraftTracker(), RecordingDecoder()
    chunk = datetime(2026, 1, 1)
    draft = ""
    for n in range(2, 32, 2):
        draft = tracker.update(chunk, seconds(n), decode)
    assert len(draft.split()) >= 28
    assert max(decode.calls) <= preview.FORCE_COMMIT_S + 2
    assert sum(decode.calls) < 0.4 * sum(range(2, 32, 2))


def test_new_utterance_resets_the_draft():
    tracker, decode = preview.DraftTracker(), RecordingDecoder()
    tracker.update(datetime(2026, 1, 1), seconds(4), decode)
    assert tracker.update(datetime(2026, 1, 2), seconds(2), decode) == "w0 w1"
    assert decode.calls[-1] == 2.0


def test_unbroken_speech_is_force_committed():
    tracker = preview.DraftTracker()
    chunk = datetime(2026, 1, 1)

    def one_segment(tail: np.ndarray) -> list[preview.Segment]:
        return [(0.0, len(tail) / SAMPLE_RATE, "nonstop")]

    tracker.update(chunk, seconds(preview.FORCE_COMMIT_S), one_segment)
    assert tracker.update(chunk, seconds(preview.FORCE_COMMIT_S + 2), one_segment) == "nonstop nonstop"


def test_silence_is_skipped_not_redecoded():
    tracker = preview.DraftTracker()
    chunk = datetime(2026, 1, 1)
    seen: list[float] = []

    def nothing(tail: np.ndarray) -> list[preview.Segment]:
        seen.append(len(tail) / SAMPLE_RATE)
        return []

    tracker.update(chunk, seconds(preview.FORCE_COMMIT_S), nothing)
    assert tracker.update(chunk, seconds(preview.FORCE_COMMIT_S + 2), nothing) == ""
    assert seen[-1] == 2.0
