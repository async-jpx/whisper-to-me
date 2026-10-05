"""Live audio chunking latency."""

from __future__ import annotations

import threading

import numpy as np

from whisper_to_me import audio


def test_continuous_speech_previews_early_without_cutting_final_audio():
    """Early drafts must leave the full-quality final chunk intact."""
    recorder = audio.Recorder()
    recorder.preview_enabled = True
    chunker = threading.Thread(target=recorder._chunk_loop)
    chunker.start()
    try:
        speech = np.full(audio.BLOCK_FRAMES, 0.1, dtype=np.float32)
        for _ in range(audio.PREVIEW_INTERVAL_BLOCKS):
            recorder._blocks.put(speech)
        preview_at, preview = recorder.previews.get(timeout=0.5)
        assert len(preview) == audio.PREVIEW_INTERVAL_BLOCKS * audio.BLOCK_FRAMES
        assert recorder.chunks.empty()
        assert recorder.active_chunk_started == preview_at

        for _ in range(audio.MAX_CHUNK_BLOCKS - audio.PREVIEW_INTERVAL_BLOCKS):
            recorder._blocks.put(speech)
        captured_at, chunk = recorder.chunks.get(timeout=0.5)
        assert captured_at is not None
        assert captured_at == preview_at
        assert len(chunk) == audio.MAX_CHUNK_BLOCKS * audio.BLOCK_FRAMES
        assert recorder.active_chunk_started is None
    finally:
        recorder._blocks.put(None)
        chunker.join(timeout=1)
