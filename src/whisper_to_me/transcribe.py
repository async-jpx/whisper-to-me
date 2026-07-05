"""Whisper transcription via faster-whisper. Runs fully offline after the
model is downloaded once from Hugging Face into the local cache."""

from __future__ import annotations

import os

import numpy as np
from faster_whisper import WhisperModel


class Transcriber:
    def __init__(self, model_size: str = "large-v3-turbo", language: str | None = None):
        self.language = language
        # num_workers=2 lets the mic and system-audio streams decode
        # concurrently (ctranslate2 handles the thread-safety).
        self.model = WhisperModel(
            model_size,
            device="auto",
            compute_type="int8",
            num_workers=2,
            cpu_threads=max(4, (os.cpu_count() or 8) // 2),
        )

    def transcribe_chunk(self, audio: np.ndarray) -> list[tuple[float, float, str]]:
        """Transcribe one utterance chunk into (start_s, end_s, text) segments.

        Offsets are relative to the chunk start, so callers can compute an
        absolute time per segment and interleave multiple sources at sentence
        granularity instead of whole-chunk granularity.
        """
        segments, info = self.model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        timed = [
            (seg.start, seg.end, seg.text.strip()) for seg in segments if seg.text.strip()
        ]
        # Lock onto the first confidently-detected language so auto-detection
        # doesn't flap between languages chunk-to-chunk on accented speech.
        if self.language is None and timed and info.language_probability > 0.7:
            self.language = info.language
        return timed

    def transcribe_file(self, path: str) -> list[tuple[float, str]]:
        """Transcribe an audio file; returns (start_seconds, text) lines."""
        segments, _ = self.model.transcribe(
            path, language=self.language, vad_filter=True, beam_size=5
        )
        return [(seg.start, seg.text.strip()) for seg in segments]
