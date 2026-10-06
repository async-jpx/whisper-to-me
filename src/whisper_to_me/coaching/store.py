"""Saved coaching data per note, in `<notes dir>/.wtm-coaching/`:
`<note name>.json` is the latest meeting review, `<note name>.voice.json` the
cached voice measurements (coaching/voice.py).

A dot-directory, so the `*.md` note globs never see it. A review whose note was
deleted or archived is simply never looked up again.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

DIR_NAME = ".wtm-coaching"


def _path(notes_dir: Path, name: str, kind: str) -> Path:
    return notes_dir / DIR_NAME / f"{name}{kind}.json"


def _write(path: Path, record: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def save(notes_dir: Path, name: str, review: dict) -> dict:
    record = {"analyzed_at": datetime.now().isoformat(timespec="seconds"), "review": review}
    _write(_path(notes_dir, name, ""), record)
    return record


def load(notes_dir: Path, name: str) -> dict | None:
    return _read(_path(notes_dir, name, ""))


def save_voice(notes_dir: Path, name: str, record: dict) -> None:
    _write(_path(notes_dir, name, ".voice"), record)


def load_voice(notes_dir: Path, name: str) -> dict | None:
    return _read(_path(notes_dir, name, ".voice"))
