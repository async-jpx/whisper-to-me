"""Microphone / system-audio capture with energy-based utterance chunking.

Audio is captured at 16 kHz mono (Whisper's native rate). The chunker
accumulates speech and flushes an utterance when it hears trailing silence,
so downstream transcription happens on natural phrase boundaries.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.1
BLOCK_FRAMES = int(SAMPLE_RATE * BLOCK_SECONDS)

# Chunking thresholds (in blocks of BLOCK_SECONDS)
TRAILING_SILENCE_BLOCKS = 8   # 0.8 s of quiet ends an utterance
MIN_SPEECH_BLOCKS = 2         # ignore blips shorter than 0.2 s
MAX_CHUNK_BLOCKS = 300        # force a flush at 30 s
PRE_ROLL_BLOCKS = 5           # 0.5 s kept from before speech onset
# Deliberately permissive: quiet speech (e.g. remote voices played through
# speakers) must get through; Whisper's own VAD rejects non-speech later.
SILENCE_RMS = 0.004


def list_devices() -> list[dict]:
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            devices.append(
                {
                    "index": idx,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "default": idx == sd.default.device[0],
                }
            )
    return devices


# Virtual devices that carry the *other* side of a call (system/loopback
# audio). Recording one of these alongside the mic captures all parties.
LOOPBACK_NAMES = ("blackhole", "zoomaudiodevice", "teams audio", "loopback")


def find_loopback_devices() -> list[dict]:
    """All system-audio input devices. Each only carries sound while its app
    is in a call, so recording all of them is harmless and covers Zoom,
    Teams, and BlackHole-routed audio at once."""
    return [
        dev
        for dev in list_devices()
        if any(marker in dev["name"].lower() for marker in LOOPBACK_NAMES)
    ]


class Recorder:
    """Captures audio blocks from an input device into utterance chunks.

    (chunk_start_time, float32 mono array) tuples are placed on `self.chunks`.
    Call `stop()` to end capture; a final partial chunk is flushed.
    """

    def __init__(self, device: int | None = None):
        self.device = device
        self.chunks: queue.Queue[tuple[datetime, np.ndarray] | None] = queue.Queue()
        self._blocks: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._chunker: threading.Thread | None = None
        self.peak_level = 0.0  # most recent block RMS, for a live meter
        # When set, chunk timestamps are epoch + sample position instead of
        # wall clock — lets replayed files (FileRecorder) share one timeline.
        self.epoch: datetime | None = None
        # Echo-cancellation hooks (see echo_cancel.EchoCanceller): the system
        # source publishes its raw blocks; the mic source filters its own.
        self.block_listener = None  # called with every raw block, None at end
        self.preprocess = None      # block -> block, applied before chunking

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata[:, 0]
        self._blocks.put(mono.copy())

    def start(self) -> None:
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        self._chunker = threading.Thread(target=self._chunk_loop, daemon=True)
        self._chunker.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._blocks.put(None)
        if self._chunker is not None:
            self._chunker.join(timeout=5)
        self.chunks.put(None)

    def _chunk_loop(self) -> None:
        buffer: list[np.ndarray] = []
        pre_roll: deque[np.ndarray] = deque(maxlen=PRE_ROLL_BLOCKS)
        chunk_started: datetime | None = None
        speech_blocks = 0
        silence_run = 0
        blocks_seen = 0

        def flush() -> None:
            nonlocal buffer, chunk_started, speech_blocks, silence_run
            if speech_blocks >= MIN_SPEECH_BLOCKS:
                self.chunks.put((chunk_started, np.concatenate(buffer)))
            buffer, chunk_started, speech_blocks, silence_run = [], None, 0, 0

        while True:
            block = self._blocks.get()
            if block is None:
                if self.block_listener is not None:
                    self.block_listener(None)
                if buffer:
                    flush()
                return
            blocks_seen += 1
            if self.block_listener is not None:
                self.block_listener(block)
            if self.preprocess is not None:
                block = self.preprocess(block)

            rms = float(np.sqrt(np.mean(block**2)))
            self.peak_level = rms
            is_speech = rms >= SILENCE_RMS

            if not buffer and not is_speech:
                pre_roll.append(block)  # keep context for the next onset
                continue

            if not buffer:
                # Speech onset: include the pre-roll so the first word
                # isn't clipped mid-phoneme.
                if self.epoch is not None:
                    first_block = blocks_seen - 1 - len(pre_roll)
                    chunk_started = self.epoch + timedelta(
                        seconds=first_block * BLOCK_SECONDS
                    )
                else:
                    chunk_started = datetime.now()
                buffer.extend(pre_roll)
                pre_roll.clear()
            buffer.append(block)
            if is_speech:
                speech_blocks += 1
                silence_run = 0
            else:
                silence_run += 1

            if silence_run >= TRAILING_SILENCE_BLOCKS or len(buffer) >= MAX_CHUNK_BLOCKS:
                flush()


_TAP_SOURCE = Path(__file__).with_name("system_audio_tap.swift")
_TAP_BINARY = Path.home() / ".cache" / "whisper-to-me" / "system-audio-tap"


def build_system_tap() -> Path | None:
    """Compile the ScreenCaptureKit helper once; None if unavailable."""
    if not shutil.which("swiftc") or not _TAP_SOURCE.exists():
        return None
    if (
        _TAP_BINARY.exists()
        and _TAP_BINARY.stat().st_mtime >= _TAP_SOURCE.stat().st_mtime
    ):
        return _TAP_BINARY
    _TAP_BINARY.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["swiftc", "-O", "-o", str(_TAP_BINARY), str(_TAP_SOURCE)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return _TAP_BINARY


class SystemAudioTap(Recorder):
    """System-audio source: hears every app's output (Zoom, Teams, browser…)
    via a ScreenCaptureKit helper process, even while speakers are muted.
    Feeds the same utterance chunker as the microphone Recorder."""

    def __init__(self, binary: Path):
        super().__init__(device=None)
        self._binary = binary
        self._proc: subprocess.Popen | None = None
        self._pump: threading.Thread | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [str(self._binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._pump = threading.Thread(target=self._pump_loop, daemon=True)
        self._pump.start()
        self._chunker = threading.Thread(target=self._chunk_loop, daemon=True)
        self._chunker.start()

    def _pump_loop(self) -> None:
        bytes_per_block = BLOCK_FRAMES * 4  # float32
        stdout = self._proc.stdout
        while True:
            data = stdout.read(bytes_per_block)
            if not data or len(data) < bytes_per_block:
                self._blocks.put(None)
                return
            self._blocks.put(np.frombuffer(data, dtype=np.float32))

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._pump is not None:
            self._pump.join(timeout=3)
        if self._chunker is not None:
            self._chunker.join(timeout=5)
        self.chunks.put(None)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


class FileRecorder(Recorder):
    """Replays an audio file through the utterance chunker as if it were a
    live source — the no-device test path behind `wtm simulate`. All
    FileRecorders in a session share one `epoch` so their chunk timestamps
    land on a common timeline and merge exactly like live sources."""

    def __init__(self, path: Path | str, epoch: datetime):
        super().__init__(device=None)
        self.epoch = epoch
        self._path = str(path)
        self._pump: threading.Thread | None = None

    def start(self) -> None:
        self._pump = threading.Thread(target=self._pump_loop, daemon=True)
        self._pump.start()
        self._chunker = threading.Thread(target=self._chunk_loop, daemon=True)
        self._chunker.start()

    def _pump_loop(self) -> None:
        from faster_whisper.audio import decode_audio  # deferred: heavy import

        samples = decode_audio(self._path, sampling_rate=SAMPLE_RATE)
        for i in range(0, len(samples), BLOCK_FRAMES):
            block = samples[i : i + BLOCK_FRAMES]
            if len(block) < BLOCK_FRAMES:
                block = np.pad(block, (0, BLOCK_FRAMES - len(block)))
            self._blocks.put(block)
        self._blocks.put(None)

    @property
    def finished(self) -> bool:
        """True once the file is fully pumped and chunked (transcription of
        already-queued chunks may still be running)."""
        return (
            self._pump is not None
            and not self._pump.is_alive()
            and self._chunker is not None
            and not self._chunker.is_alive()
        )

    def stop(self) -> None:
        if self._pump is not None:
            self._pump.join(timeout=10)
        if self._chunker is not None:
            self._chunker.join(timeout=10)
        self.chunks.put(None)
