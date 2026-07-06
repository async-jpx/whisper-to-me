"""Speaker diarization (Phase 4.1, beta): the clustering and labeling logic,
tested with synthetic embeddings so torch/speechbrain aren't needed. The real
ECAPA model path is exercised separately (behavioral, mic-free) and its quality
is a known follow-up to tune on real voices."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

import whisper_to_me.diarize as diarize


def _v(*vals) -> np.ndarray:
    return np.array(vals, dtype=np.float32)


def test_cluster_two_tight_groups():
    labels = diarize.cluster([_v(1, 0, 0), _v(0.98, 0.02, 0), _v(0, 1, 0), _v(0.02, 0.98, 0)])
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert len(set(labels)) == 2


def test_cluster_single_group():
    labels = diarize.cluster([_v(1, 0, 0), _v(0.99, 0.01, 0), _v(0.98, 0.0, 0.02)])
    assert len(set(labels)) == 1


def test_cluster_caps_at_max_speakers():
    vs = [np.eye(6, dtype=np.float32)[i] for i in range(6)]  # 6 mutually distant
    assert len(set(diarize.cluster(vs))) <= diarize.MAX_SPEAKERS


def test_cluster_empty_and_single():
    assert diarize.cluster([]) == []
    assert diarize.cluster([_v(1, 0, 0)]) == [0]


def _others(seconds_and_group: list[tuple[float, str]]):
    """Build time-sorted Others lines + an embeddings dict; group 'A'/'B'
    become orthogonal vectors."""
    t0 = datetime(2026, 7, 6, 10, 0, 0)
    lines, embs = [], {}
    for i, (sec, group) in enumerate(seconds_and_group):
        t = t0 + timedelta(seconds=sec)
        txt = f"line{i}"
        lines.append((t, 3.0, "Others", txt))
        embs[(t, txt)] = _v(1, 0, 0) if group == "A" else _v(0, 1, 0)
    return lines, embs


def test_assign_labels_two_speakers_by_first_appearance():
    lines, embs = _others([(0, "A"), (5, "B"), (10, "A"), (15, "B")])
    labels = diarize.assign_labels(lines, embs)
    assert labels[(lines[0][0], "line0")] == "Speaker A"
    assert labels[(lines[1][0], "line1")] == "Speaker B"
    assert labels[(lines[2][0], "line2")] == "Speaker A"
    assert labels[(lines[3][0], "line3")] == "Speaker B"


def test_assign_labels_single_speaker_returns_empty():
    lines, embs = _others([(0, "A"), (5, "A"), (10, "A")])
    assert diarize.assign_labels(lines, embs) == {}


def test_assign_labels_tiny_cluster_below_share_returns_empty():
    # 12 dominant A utterances vs one lone B (1/13 ≈ 8% < MIN_CLUSTER_SHARE):
    # no second *significant* speaker → keep everything "Others".
    lines, embs = _others([(i * 5, "A") for i in range(12)] + [(100, "B")])
    assert diarize.assign_labels(lines, embs) == {}


def test_assign_labels_unembedded_line_takes_nearest():
    lines, embs = _others([(0, "A"), (5, "B"), (10, "A"), (15, "B")])
    t_un = lines[0][0] + timedelta(seconds=1)
    lines.append((t_un, 3.0, "Others", "gap"))
    lines.sort(key=lambda ln: ln[0])
    labels = diarize.assign_labels(lines, embs)
    assert labels[(t_un, "gap")] == "Speaker A"  # nearest labeled is line0 (A)


def test_assign_labels_ignores_you_lines():
    lines, embs = _others([(0, "A"), (5, "B"), (10, "A"), (15, "B")])
    you = (lines[0][0] + timedelta(seconds=2), 3.0, "You", "my words")
    labels = diarize.assign_labels(lines + [you], embs)
    assert (you[0], "my words") not in labels  # You is never relabeled
