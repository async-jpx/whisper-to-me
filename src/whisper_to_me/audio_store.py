"""Opt-in meeting audio retention (`[recording] keep_audio` in config.toml).

A kept recording is ONE mono track per meeting — mic and system audio mixed
on the shared capture timeline, so second `t` of the file is transcript stamp
`t` — stored as AAC next to its note:

    <note dir>/.wtm-audio/<note stem>.m4a
    <note dir>/.wtm-audio/<note stem>.peaks.json   # waveform for the player

The hidden directory sits outside every `*.md` glob, so a recording is never a
note, never indexed, never exported. It always lives beside its note's
directory (notes dir or Archive/), and moves/deletes with the note.

While recording, each source streams int16 samples into its own temp file
(one writer per file — the chunker thread of that source), seeking to the
block's position on the capture timeline; gaps (a late-starting or stalled
system tap) are holes that read back as silence. At stop the tracks are mixed
into a WAV and handed to macOS `afconvert`. Temp files live in a per-process
directory so a crash leaves something the next start can identify as orphaned
(its pid is gone) without ever touching a live session's files.

Recordings nobody played for UNPLAYED_DAYS are purged (mtime = last play).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE

AUDIO_DIRNAME = ".wtm-audio"
UNPLAYED_DAYS = 30
_TEMP_PREFIX = ".tmp-"
# A live source whose sample count falls this far behind wall clock skipped
# audio (helper stall): jump ahead so later blocks stay on the timeline.
_RESYNC_SAMPLES = SAMPLE_RATE // 2
PEAK_SECONDS = 0.5
MAX_PEAKS = 2000
_AAC_BITRATE = "32000"

BlockTap = Callable[[datetime, np.ndarray], None]


def audio_dir(note: Path) -> Path:
    return note.parent / AUDIO_DIRNAME


def audio_path(note: Path) -> Path:
    return audio_dir(note) / f"{note.stem}.m4a"


def peaks_path(note: Path) -> Path:
    return audio_dir(note) / f"{note.stem}.peaks.json"


def has_audio(note: Path) -> bool:
    return audio_path(note).is_file()


def mark_played(note: Path) -> None:
    """Retention clock: a recording counts as used whenever it is fetched."""
    os.utime(audio_path(note))


def move_audio(src_note: Path, dest_note: Path) -> None:
    """Make the recording follow a note that moved or got renamed."""
    for src, dest in (
        (audio_path(src_note), audio_path(dest_note)),
        (peaks_path(src_note), peaks_path(dest_note)),
    ):
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dest)


def delete_audio(note: Path) -> None:
    audio_path(note).unlink(missing_ok=True)
    peaks_path(note).unlink(missing_ok=True)


def purge_unplayed(note_dirs: list[Path], now: float | None = None) -> int:
    """Delete recordings in these note directories not played for
    UNPLAYED_DAYS (the notes stay). Returns how many were removed."""
    cutoff = (now if now is not None else time.time()) - UNPLAYED_DAYS * 86400
    removed = 0
    for note_dir in note_dirs:
        for m4a in (note_dir / AUDIO_DIRNAME).glob("*.m4a"):
            try:
                if m4a.stat().st_mtime >= cutoff:
                    continue
            except FileNotFoundError:
                continue
            delete_audio(note_dir / f"{m4a.stem}.md")
            removed += 1
    return removed


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clean_temp(notes_dir: Path) -> None:
    """Remove temp tracks left by a process that died mid-recording."""
    for directory in (notes_dir / AUDIO_DIRNAME).glob(f"{_TEMP_PREFIX}*"):
        pid = directory.name[len(_TEMP_PREFIX) :].split("-", 1)[0]
        if pid.isdigit() and _pid_alive(int(pid)):
            continue
        shutil.rmtree(directory, ignore_errors=True)


class _Track:
    """One source's samples on disk, placed by capture time."""

    def __init__(self, path: Path, started: datetime) -> None:
        self.path = path
        self._started = started
        self._fh = path.open("wb")
        self._cursor: int | None = None
        self._lock = threading.Lock()
        self.error: OSError | None = None

    def write(self, at: datetime, block: np.ndarray) -> None:
        target = max(0, round((at - self._started).total_seconds() * SAMPLE_RATE))
        pcm = (np.clip(block, -1.0, 1.0) * 32767).astype("<i2")
        with self._lock:
            if self._fh is None:
                return  # a straggling block after stop
            if self._cursor is None or target - self._cursor > _RESYNC_SAMPLES:
                self._cursor = target
            # Runs on the source's chunker thread: raising here would stop
            # that source's transcription, so a disk error only ends the track.
            try:
                self._fh.seek(self._cursor * 2)
                self._fh.write(pcm.tobytes())
            except OSError as exc:
                self.error = exc
                self._fh.close()
                self._fh = None
                return
            self._cursor += len(pcm)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    @property
    def samples(self) -> int:
        return self.path.stat().st_size // 2 if self.path.exists() else 0


class AudioCapture:
    """Records every source of one session into `audio_path(note)`."""

    def __init__(self, note: Path, started: datetime) -> None:
        self._note = note
        self._started = started
        clean_temp(note.parent)
        self._tmp = audio_dir(note) / f"{_TEMP_PREFIX}{os.getpid()}-{note.stem}"
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._tracks: list[_Track] = []

    def track(self) -> BlockTap:
        """A writer for one more source: call it with (capture time, block)."""
        track = _Track(self._tmp / f"{len(self._tracks)}.pcm", self._started)
        self._tracks.append(track)
        return track.write

    def finish(self) -> Path | None:
        """Mix, encode, write the peaks sidecar; None when nothing was heard.
        Temp files are removed whatever happens."""
        try:
            for track in self._tracks:
                track.close()
                if track.error is not None:
                    raise track.error
            total = max((t.samples for t in self._tracks), default=0)
            if total == 0:
                return None
            wav = self._tmp / "mix.wav"
            peaks = _mix(self._tracks, total, wav)
            encoded = self._tmp / "mix.m4a"
            subprocess.run(
                ["afconvert", "-f", "m4af", "-d", "aac", "-b", _AAC_BITRATE,
                 str(wav), str(encoded)],
                check=True,
                capture_output=True,
            )
            sidecar = self._tmp / "peaks.json"
            sidecar.write_text(
                json.dumps({"duration_s": round(total / SAMPLE_RATE, 3), "peaks": peaks}),
                encoding="utf-8",
            )
            # Peaks land first: has_audio() keys off the m4a, so a visible
            # recording always has its waveform.
            sidecar.replace(peaks_path(self._note))
            dest = audio_path(self._note)
            encoded.replace(dest)
            return dest
        finally:
            self.discard()

    def discard(self) -> None:
        for track in self._tracks:
            track.close()
        shutil.rmtree(self._tmp, ignore_errors=True)


def _mix(tracks: list[_Track], total: int, wav_path: Path) -> list[float]:
    """Sum the tracks into a 16-bit WAV, streaming; return normalized peaks."""
    window = max(int(SAMPLE_RATE * PEAK_SECONDS), math.ceil(total / MAX_PEAKS))
    chunk = window * max(1, (SAMPLE_RATE * 10) // window)
    peaks: list[float] = []
    handles = [t.path.open("rb") for t in tracks]
    try:
        with wave.open(str(wav_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            for offset in range(0, total, chunk):
                n = min(chunk, total - offset)
                mixed = np.zeros(n, dtype=np.int32)
                for fh in handles:
                    part = np.frombuffer(fh.read(n * 2), dtype="<i2")
                    mixed[: len(part)] += part
                pcm = np.clip(mixed, -32768, 32767).astype("<i2")
                out.writeframes(pcm.tobytes())
                level = np.abs(pcm.astype(np.int32))
                for i in range(0, n, window):
                    peaks.append(float(level[i : i + window].max()))
    finally:
        for fh in handles:
            fh.close()
    top = max(peaks, default=0.0)
    return [round(p / top, 3) if top else 0.0 for p in peaks]
