"""Voice measurements from the user's own audio track.

The source is the kept mic-only track (audio_store.mic_path), or the main
recording when the note has a single source (then it IS the mic). A note
recorded with other speakers but no mic track has no voice measurements:
the mixed track cannot tell whose voice is whose.

Pace, pauses, loudness and pitch spread are measured facts about the signal.
Confidence and emotion are interpretations, so they stay out of here.
Results are cached in the coaching store, so they outlive the 30-day audio
purge.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from .. import audio_store, notes
from . import store

VERSION = 1  # bump when a measurement changes; stale caches are recomputed
RATE = 16_000
HOP = RATE // 50  # 20 ms frames
FRAME = 2 * HOP  # 40 ms analysis window
F0_MIN, F0_MAX = 75.0, 400.0
VOICED_CORR = 0.5  # normalized autocorrelation peak needed to call a frame voiced
SPEECH_FLOOR_DB = -50.0
ONSET_S = 2.0
REF_BELOW_DB = 20.0
PAUSE_S = 0.5
STOP_S = 2.0
MIN_SPEECH_S = 5.0


def source(note: Path, lines: list[notes.TranscriptLine]) -> Path | None:
    mic = audio_store.mic_path(note)
    if mic.is_file():
        return mic
    if audio_store.has_audio(note) and all(line.speaker is None for line in lines):
        return audio_store.audio_path(note)
    return None


def decode(path: Path) -> np.ndarray:
    """Decode any afconvert-readable file to float32 mono at 16 kHz."""
    with tempfile.TemporaryDirectory(prefix="wtm-voice-") as directory:
        wav_path = Path(directory) / "voice.wav"
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1",
             str(path), str(wav_path)],
            check=True, capture_output=True, timeout=300,
        )
        with wave.open(str(wav_path)) as wav:
            pcm = wav.readframes(wav.getnframes())
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768


def _frames(samples: np.ndarray) -> np.ndarray:
    count = max(0, (len(samples) - FRAME) // HOP + 1)
    if count == 0:
        return np.zeros((0, FRAME), dtype=np.float32)
    index = np.arange(FRAME)[None, :] + HOP * np.arange(count)[:, None]
    return samples[index]


def _pitch(frames: np.ndarray) -> np.ndarray:
    """Per-frame f0 in Hz, NaN when unvoiced.

    Normalized autocorrelation is divided by the window's own autocorrelation
    (Boersma 1993), so long periods are not under-scored. The SHORTEST period
    near the best peak wins, because every multiple of the true period scores
    about as high, which would cause octave errors."""
    lo, hi = int(RATE / F0_MAX), int(RATE / F0_MIN)
    out = np.full(len(frames), np.nan, dtype=np.float32)
    window = np.hanning(FRAME).astype(np.float32)

    def autocorr(x: np.ndarray) -> np.ndarray:
        corr = np.fft.irfft(np.abs(np.fft.rfft(x, n=2 * FRAME, axis=-1)) ** 2, axis=-1)
        return corr[..., : hi + 2]

    window_corr = autocorr(window)
    window_corr = window_corr / window_corr[0]
    for start in range(0, len(frames), 4096):
        block = frames[start:start + 4096]
        corr = autocorr((block - block.mean(axis=1, keepdims=True)) * window)
        with np.errstate(divide="ignore", invalid="ignore"):
            norm = np.where(corr[:, :1] > 0, corr / corr[:, :1], 0.0) / window_corr
        inner = norm[:, lo:hi + 1]
        peaks = (inner >= norm[:, lo - 1:hi]) & (inner >= norm[:, lo + 1:hi + 2])
        best = np.max(np.where(peaks, inner, -np.inf), axis=1)
        strong = peaks & (inner >= 0.9 * best[:, None])
        lag = lo + np.argmax(strong, axis=1)
        voiced = np.isfinite(best) & (best >= VOICED_CORR) & strong.any(axis=1)
        out[start:start + len(block)][voiced] = RATE / lag[voiced]
    return out


def measure(samples: np.ndarray, lines: list[notes.TranscriptLine]) -> dict | None:
    """Voice metrics for the user's own track, None under MIN_SPEECH_S of speech.

    The track holds only the user, so no transcript window cuts it. Someone
    talking over the user must not shorten their turn. Speech starts at the
    user's first line (echo cancellation is still converging before it) and is
    gated REF_BELOW_DB under the user's level at the start of their lines,
    which is reliably their voice, so residual echo stays out. Talk time joins
    speech across gaps shorter than STOP_S; a longer gap means the user
    stopped, and counts as neither talk time nor a pause."""
    mine = [line for line in lines if line.speaker in (None, "You")]
    frames = _frames(samples)
    if not mine or not len(frames):
        return None
    frame_s = HOP / RATE
    level_db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    onsets = np.concatenate([
        level_db[int(line.t / frame_s):int((line.t + ONSET_S) / frame_s)] for line in mine
    ])
    if not len(onsets):
        return None
    threshold = max(SPEECH_FLOOR_DB, float(np.percentile(onsets, 90)) - REF_BELOW_DB)
    speech = level_db >= threshold
    speech[:int(mine[0].t / frame_s)] = False
    active = np.flatnonzero(speech)
    if len(active) * frame_s < MIN_SPEECH_S:
        return None
    gaps = (np.diff(active) - 1) * frame_s
    stops = gaps >= STOP_S
    bounds = np.flatnonzero(stops)
    firsts = np.r_[active[0], active[bounds + 1]]
    lasts = np.r_[active[bounds], active[-1]]
    talk_s = float(np.sum(lasts - firsts + 1)) * frame_s
    pauses = int(np.sum((gaps >= PAUSE_S) & ~stops))
    words = sum(len(line.text.split()) for line in mine)
    f0 = _pitch(frames[speech])
    f0 = f0[~np.isnan(f0)]
    semitones = 12 * np.log2(f0 / np.median(f0)) if len(f0) >= 25 else None
    loud = level_db[speech]
    return {
        "speaking_seconds": round(talk_s),
        "speaking_rate_wpm": round(words * 60 / talk_s),
        "pauses_per_minute": round(float(pauses * 60 / talk_s), 1),
        "loudness_median_dbfs": round(float(np.median(loud)), 1),
        "loudness_range_db": round(float(np.percentile(loud, 90) - np.percentile(loud, 10)), 1),
        "pitch_median_hz": round(float(np.median(f0))) if semitones is not None else None,
        "pitch_range_semitones": (
            round(float(np.percentile(semitones, 90) - np.percentile(semitones, 10)), 1)
            if semitones is not None else None
        ),
    }


def voice_metrics(note: Path) -> dict | None:
    """Cached measure() for a note, None when there is no voice-only audio."""
    cached = store.load_voice(note.parent, note.name)
    if cached is not None and cached.get("version") == VERSION:
        return cached.get("metrics")
    lines = notes.parse_transcript(note.read_text(encoding="utf-8"))
    path = source(note, lines)
    if path is None:
        return None
    try:
        metrics = measure(decode(path), lines)
    except (OSError, subprocess.SubprocessError, wave.Error, ValueError):
        return None
    store.save_voice(note.parent, note.name, {"version": VERSION, "metrics": metrics})
    return metrics
