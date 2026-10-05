"""Draft transcript flow without opening an audio device."""

from __future__ import annotations

import threading
import time

import numpy as np

from whisper_to_me import audio, session


class BlockRecorder(audio.Recorder):
    def start(self) -> None:
        self._chunker = threading.Thread(target=self._chunk_loop, daemon=True)
        self._chunker.start()


class FakeTranscriber:
    def transcribe_chunk(self, chunk):
        return [(0.0, len(chunk) / audio.SAMPLE_RATE, "final words")]


class FakePreviewer:
    def transcribe_preview(self, chunk):
        return "draft words"


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("expected event did not arrive")


def test_both_sources_show_drafts_then_save_only_final_lines(tmp_path):
    mic, system = BlockRecorder(), BlockRecorder()
    emitted = []
    stop = threading.Event()
    result = {}

    def record():
        result["value"] = session.record_session(
            FakeTranscriber(), "Preview test", tmp_path,
            sources=[("You", mic), ("Others", system)],
            use_aec=False, keep_echoes=True, stop_event=stop,
            preview_factory=FakePreviewer, events=emitted.append,
        )

    thread = threading.Thread(target=record)
    thread.start()
    try:
        wait_for(lambda: mic._chunker is not None and system._chunker is not None)
        speech = np.full(audio.BLOCK_FRAMES, 0.1, dtype=np.float32)
        quiet = np.zeros(audio.BLOCK_FRAMES, dtype=np.float32)
        for recorder in (mic, system):
            for _ in range(audio.PREVIEW_INTERVAL_BLOCKS):
                recorder._blocks.put(speech)
        wait_for(lambda: {e.get("source") for e in emitted if e["type"] == "partial"}
                 == {"You", "Others"})
        assert not any(e["type"] == "line" for e in emitted)

        for recorder in (mic, system):
            for _ in range(audio.TRAILING_SILENCE_BLOCKS):
                recorder._blocks.put(quiet)
        wait_for(lambda: len([e for e in emitted if e["type"] == "partial_clear"]) == 2)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert len([e for e in emitted if e["type"] == "line"]) == 2
    assert len(result["value"][0]) == 2
    note = next(tmp_path.glob("*.md")).read_text()
    assert "final words" in note
    assert "draft words" not in note
