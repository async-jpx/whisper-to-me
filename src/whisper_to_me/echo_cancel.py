"""Acoustic echo cancellation: subtract system audio from the mic signal.

When remote voices play through the speakers, the mic hears them. The system
tap carries exactly what the speakers play, so it is a perfect reference: an
adaptive filter learns the speaker→mic path and subtracts the echo from every
mic block *before* utterance chunking, leaving the mic stream with only the
user's own voice (the text-level dedup filter remains as a backstop for the
convergence window and residuals).

Pieces, all plain numpy:
- delay lock: the two streams start at different moments and the acoustic
  path adds more; a coarse block-RMS envelope correlation finds the offset to
  within one block, then a raw cross-correlation refines it to the sample.
- FDAF: single-partition frequency-domain NLMS (overlap-save, 100 ms filter)
  cancels the echo once the delay is locked.
- double-talk: adaptation freezes while the user speaks (Geigel detector) so
  the filter never learns to cancel the user's own voice.

Both streams are indexed in samples since their own start; on one machine at
one nominal rate the index gap between them is (nearly) constant, and the
delay estimate absorbs it. In replay mode (`wtm simulate`) the two sides
rendezvous — the mic waits for the reference to reach its position, the
reference throttles to stay at most REPLAY_SLACK ahead — so unpaced file
pumps behave like a (fast) live session.
"""

from __future__ import annotations

import threading

import numpy as np

from .audio import BLOCK_FRAMES, SAMPLE_RATE

N = BLOCK_FRAMES              # samples per processed block (0.1 s)
FFT = 2 * N                   # overlap-save FFT size; filter length is N
RING_SECONDS = 12.0           # reference history; must cover the start offset
MIC_RING_SECONDS = 4.0        # raw mic history for the fine delay estimate
MAX_DELAY_BLOCKS = 80         # search the coarse delay up to 8 s
ENV_WINDOW = 600              # most recent block-pairs used per estimate (60 s)
COARSE_EVERY = 20             # try to (re)lock every 2 s until locked
RECHECK_EVERY = 100           # once locked, re-check the delay every 10 s
MIN_ENV_BLOCKS = 30           # need 3 s of overlapping history to estimate
MIN_COARSE_CORR = 0.5         # envelope Pearson correlation to accept a lock
FINE_SPAN = 3200              # fine search ±0.2 s around the coarse estimate
FINE_WINDOW = 16_000          # 1 s of raw audio for the fine estimate
RELOCK_JUMP = 800             # re-lock (and reset the filter) if the delay
                              # drifts by more than this many samples
PRE_SHIFT = 400               # start the filter 25 ms *before* the estimated
                              # delay so jitter in either direction stays
                              # inside the filter's 100 ms span
MU = 0.5                      # NLMS step size
GEIGEL = 0.75                 # double-talk: freeze if |mic| > 0.75·max|ref|
REF_ACTIVE_RMS = 1e-3         # no reference energy → nothing to cancel/learn
REPLAY_SLACK = 2 * SAMPLE_RATE  # replay rendezvous: ref stays ≤ 2 s ahead


class _Ring:
    """Fixed-size ring buffer addressed by absolute sample index."""

    def __init__(self, capacity: int):
        self._buf = np.zeros(capacity, dtype=np.float32)
        self.capacity = capacity
        self.end = 0  # absolute index one past the last written sample

    def write(self, samples: np.ndarray) -> None:
        n = len(samples)
        pos = self.end % self.capacity
        first = min(n, self.capacity - pos)
        self._buf[pos : pos + first] = samples[:first]
        if first < n:
            self._buf[: n - first] = samples[first:]
        self.end += n

    def read(self, start: int, length: int) -> np.ndarray:
        """Samples [start, start+length); zeros where nothing was written."""
        out = np.zeros(length, dtype=np.float32)
        lo = max(start, self.end - self.capacity, 0)
        hi = min(start + length, self.end)
        if lo >= hi:
            return out
        idx = np.arange(lo, hi) % self.capacity
        out[lo - start : hi - start] = self._buf[idx]
        return out


class EchoCanceller:
    """Feed system-audio blocks via add_reference(); pass mic blocks through
    process(). Wire-up: `tap.block_listener = ec.add_reference`,
    `mic.preprocess = ec.process` (see session.record_session)."""

    def __init__(self, realtime: bool = True):
        self._realtime = realtime
        self._ref = _Ring(int(RING_SECONDS * SAMPLE_RATE))
        self._mic = _Ring(int(MIC_RING_SECONDS * SAMPLE_RATE))
        # Block-RMS envelopes, one entry per block since each stream's start.
        # A few floats per second — keeping them whole keeps them aligned.
        self._ref_env: list[float] = []
        self._mic_env: list[float] = []
        self._cond = threading.Condition()
        self._ref_done = False
        self._m = 0            # absolute mic sample index (process() calls)
        self._delay: int | None = None
        self._weights = np.zeros(N + 1, dtype=np.complex128)  # rfft bins
        self.cancelling = False  # exposed for logs/tests

    # -- reference side (system-source chunker thread) ------------------------

    def add_reference(self, block: np.ndarray | None) -> None:
        with self._cond:
            if block is None:
                self._ref_done = True
            else:
                if not self._realtime:
                    # Rendezvous: don't run ahead of the mic replay, or the
                    # ring/envelope histories stop lining up.
                    self._cond.wait_for(
                        lambda: self._ref.end - self._m <= REPLAY_SLACK,
                        timeout=10,
                    )
                self._ref.write(block)
                self._ref_env.append(float(np.sqrt(np.mean(block**2))))
            self._cond.notify_all()

    # -- mic side (mic chunker thread) -----------------------------------------

    def process(self, block: np.ndarray) -> np.ndarray:
        m = self._m
        self._mic.write(block)
        self._mic_env.append(float(np.sqrt(np.mean(block**2))))
        with self._cond:
            self._m = m + len(block)
            if not self._realtime:
                # Replayed files are pumped as fast as possible; wait for the
                # reference stream to reach this block's position.
                self._cond.wait_for(
                    lambda: self._ref.end >= m + len(block) or self._ref_done,
                    timeout=10,
                )
            self._cond.notify_all()

        blocks_done = m // N
        interval = RECHECK_EVERY if self._delay is not None else COARSE_EVERY
        if blocks_done % interval == 0:
            self._update_delay()

        if self._delay is None:
            return block
        return self._cancel(block, m)

    # -- delay estimation --------------------------------------------------------

    def _update_delay(self) -> None:
        with self._cond:
            ref_env = np.array(self._ref_env)
        mic_env = np.array(self._mic_env)
        if len(ref_env) < MIN_ENV_BLOCKS or len(mic_env) < MIN_ENV_BLOCKS:
            return
        if float(ref_env[-min(len(ref_env), ENV_WINDOW) :].mean()) < REF_ACTIVE_RMS:
            return  # speakers essentially silent: no signal to align on

        # Envelopes are per-stream block indices; pairing mic[i+d] with
        # ref[i] tests "mic lags the reference by d blocks".
        best_d, best_corr = 0, 0.0
        for d in range(MAX_DELAY_BLOCKS + 1):
            end = min(len(mic_env) - d, len(ref_env))
            if end < MIN_ENV_BLOCKS:
                break
            start = max(0, end - ENV_WINDOW)
            a = mic_env[d + start : d + end]
            b = ref_env[start:end]
            a = a - a.mean()
            b = b - b.mean()
            denom = float(np.linalg.norm(a) * np.linalg.norm(b))
            if denom == 0:
                continue
            corr = float(np.dot(a, b)) / denom
            if corr > best_corr:
                best_d, best_corr = d, corr
        if best_corr < MIN_COARSE_CORR:
            return

        delay = self._fine_delay(best_d * N)
        if delay is None:
            return
        if self._delay is None or abs(delay - self._delay) > RELOCK_JUMP:
            self._delay = max(delay, 0)
            self._weights[:] = 0  # new alignment: relearn the echo path
            self.cancelling = True

    def _fine_delay(self, coarse: int) -> int | None:
        m = self._m
        mic_seg = self._mic.read(m - FINE_WINDOW, FINE_WINDOW)
        ref_start = m - FINE_WINDOW - coarse - FINE_SPAN
        needed = FINE_WINDOW + 2 * FINE_SPAN
        with self._cond:
            if (
                ref_start < max(self._ref.end - self._ref.capacity, 0)
                or self._ref.end < ref_start + FINE_WINDOW + FINE_SPAN
            ):
                return None  # window (partly) evicted or not yet captured
            ref_seg = self._ref.read(ref_start, needed)
        if not mic_seg.any() or not ref_seg.any():
            return None
        corr = np.correlate(ref_seg, mic_seg, mode="valid")
        k = int(np.argmax(corr))
        # mic[n] ≈ g·ref_seg[n+k]  →  delay = mic_abs(n) − ref_abs(n+k)
        return (m - FINE_WINDOW) - (ref_start + k)

    # -- cancellation (overlap-save FDAF) -------------------------------------

    def _cancel(self, block: np.ndarray, m: int) -> np.ndarray:
        if len(block) != N:  # only ever the final partial block of a stream
            return block
        d = self._delay - PRE_SHIFT
        with self._cond:
            ref_seg = self._ref.read(m - d - N, FFT)
        ref_rms = float(np.sqrt(np.mean(ref_seg[N:] ** 2)))
        if ref_rms < REF_ACTIVE_RMS:
            return block  # speakers silent: nothing to cancel or learn

        x = np.fft.rfft(ref_seg)
        y_hat = np.fft.irfft(x * self._weights)[N:]
        e = (block - y_hat).astype(np.float32)

        # Geigel double-talk detector: while the user speaks, freeze
        # adaptation so the filter never learns to cancel their voice.
        # Always adapt on the *true* error — that is what lets a mis-adapted
        # filter correct itself.
        if float(np.max(np.abs(block))) <= GEIGEL * float(np.max(np.abs(ref_seg))):
            spectrum = np.fft.rfft(np.concatenate([np.zeros(N, dtype=np.float32), e]))
            power = (x * np.conj(x)).real
            # Regularize against the mean bin power: near-empty bins must not
            # take huge steps (they otherwise blow the filter up).
            power = power + 0.01 * float(power.mean()) + 1e-12
            self._weights += MU * np.conj(x) * spectrum / power
            w = np.fft.irfft(self._weights)
            w[N:] = 0  # overlap-save gradient constraint
            self._weights = np.fft.rfft(w)

        if float(np.sqrt(np.mean(e**2))) > 1.05 * float(np.sqrt(np.mean(block**2))):
            return block  # never output something worse than the raw mic
        return e
