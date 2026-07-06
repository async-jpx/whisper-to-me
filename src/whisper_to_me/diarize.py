"""Speaker diarization within the "Others" source (beta).

We already know You vs Others; this splits Others into "Speaker A/B/C" by
embedding each Others utterance with a local ECAPA-TDNN model (SpeechBrain) and
agglomeratively clustering the embeddings on cosine distance. Post-hoc: labels
land in the saved note; the live view keeps showing "Others".

Optional and privacy-clean: requires `uv sync --extra diarize` (torch is
heavy), and the ECAPA model downloads once from Hugging Face into the local
cache, then runs fully offline — the same precedent as faster-whisper. When the
extra is missing, `available()` is False and callers keep plain "Others".

Quality is beta and the thresholds below are tuned on synthetic voices only;
real-meeting audio will likely need retuning.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime

import numpy as np
from rich.console import Console

from .dedup import CLEAN_SPEAKER, Line

console = Console()

SAMPLE_RATE = 16_000
MIN_SEGMENT_S = 1.0  # too short to embed reliably
COSINE_THRESHOLD = 0.68  # merge clusters whose cosine distance is below this
MAX_SPEAKERS = 4
MIN_CLUSTER_SHARE = 0.10  # a "speaker" owning <10% of embedded speech is noise

_AVAILABLE: bool | None = None


def available() -> bool:
    """True when the optional diarization stack is importable (cached)."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            _AVAILABLE = (
                importlib.util.find_spec("speechbrain") is not None
                and importlib.util.find_spec("torch") is not None
            )
        except (ImportError, ValueError):
            _AVAILABLE = False
    return _AVAILABLE


class SpeakerEmbedder:
    """Lazily-loaded ECAPA embedder. embed() returns a 1-D vector, or None for
    a too-short segment or any model error — a failed embedding must never kill
    a transcription worker."""

    def __init__(self) -> None:
        self._model = None
        self._warned = False

    def _load(self):
        if self._model is None:
            from pathlib import Path

            from speechbrain.inference.classifiers import EncoderClassifier

            cache = Path.home() / ".cache" / "whisper-to-me" / "ecapa"
            self._model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(cache),
                run_opts={"device": "cpu"},
            )
        return self._model

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        if audio is None or len(audio) < int(MIN_SEGMENT_S * SAMPLE_RATE):
            return None
        try:
            import torch

            model = self._load()
            wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)).unsqueeze(0)
            with torch.no_grad():
                emb = model.encode_batch(wav)
            return emb.squeeze().cpu().numpy().astype(np.float32)
        except Exception as exc:  # any model/API failure → degrade, don't crash
            if not self._warned:
                console.print(
                    f"[yellow]Diarization embedding failed ({exc}); "
                    "continuing without speaker labels.[/yellow]"
                )
                self._warned = True
            return None


def _normalize(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-8)


def _avg_linkage_distance(x: np.ndarray, a: list[int], b: list[int]) -> float:
    """Average cosine distance between two clusters of L2-normalized rows."""
    sims = x[a] @ x[b].T
    return 1.0 - float(sims.mean())


def cluster(embeddings: list[np.ndarray]) -> list[int]:
    """Average-linkage agglomerative clustering on cosine distance. Merges the
    closest pair until it exceeds COSINE_THRESHOLD, but never leaves more than
    MAX_SPEAKERS clusters. Plain numpy — the corpus is a few hundred vectors."""
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]
    x = np.array([_normalize(np.asarray(e, dtype=np.float32)) for e in embeddings])
    clusters: list[list[int]] = [[i] for i in range(n)]

    while len(clusters) > 1:
        best_d, best_a, best_b = None, 0, 1
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                d = _avg_linkage_distance(x, clusters[a], clusters[b])
                if best_d is None or d < best_d:
                    best_d, best_a, best_b = d, a, b
        # Stop only when the nearest pair is far apart AND we are within cap.
        if best_d >= COSINE_THRESHOLD and len(clusters) <= MAX_SPEAKERS:
            break
        clusters[best_a].extend(clusters[best_b])
        del clusters[best_b]

    labels = [0] * n
    for cid, members in enumerate(clusters):
        for idx in members:
            labels[idx] = cid
    return labels


def _speaker_name(i: int) -> str:
    return f"Speaker {chr(ord('A') + i)}"


def assign_labels(
    lines: list[Line],
    embeddings: dict[tuple[datetime, str], np.ndarray],
) -> dict[tuple[datetime, str], str]:
    """Map each "Others" line to a speaker label. Keys are (captured_at, text).

    Returns {} — meaning keep plain "Others" — unless at least two clusters
    each own ≥ MIN_CLUSTER_SHARE of the embedded speech time. Un-embedded (or
    noise-cluster) lines take the label of the nearest-in-time labeled line.
    Only the kept, sorted `lines` are considered, so echo-dropped lines never
    vote.
    """
    others = [ln for ln in lines if ln[2] == CLEAN_SPEAKER]
    embedded = [ln for ln in others if (ln[0], ln[3]) in embeddings]
    if len(embedded) < 2:
        return {}

    vecs = [embeddings[(ln[0], ln[3])] for ln in embedded]
    cids = cluster(vecs)

    total = sum(ln[1] for ln in embedded) or 1.0
    dur_by_cluster: dict[int, float] = {}
    for ln, cid in zip(embedded, cids):
        dur_by_cluster[cid] = dur_by_cluster.get(cid, 0.0) + ln[1]
    significant = {c for c, d in dur_by_cluster.items() if d / total >= MIN_CLUSTER_SHARE}
    if len(significant) < 2:
        return {}

    # Letter each significant cluster by first appearance (lines are time-sorted).
    order: list[int] = []
    for cid in cids:
        if cid in significant and cid not in order:
            order.append(cid)
    cluster_label = {cid: _speaker_name(i) for i, cid in enumerate(order)}

    # Directly labeled lines (embedded + in a significant cluster).
    labels: dict[tuple[datetime, str], str] = {}
    labeled_points: list[tuple[datetime, str]] = []  # (time, label) for nearest lookup
    for ln, cid in zip(embedded, cids):
        if cid in significant:
            name = cluster_label[cid]
            labels[(ln[0], ln[3])] = name
            labeled_points.append((ln[0], name))

    # Everything else (un-embedded or noise-cluster) → nearest labeled line.
    for ln in others:
        key = (ln[0], ln[3])
        if key in labels:
            continue
        nearest = min(labeled_points, key=lambda p: abs((p[0] - ln[0]).total_seconds()))
        labels[key] = nearest[1]
    return labels
