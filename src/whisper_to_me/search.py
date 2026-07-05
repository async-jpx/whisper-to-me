"""Full-text search over the notes directory — SQLite FTS5, one local file.

The index lives inside the notes directory (`.wtm-index.sqlite3`, hidden and
outside the `*.md` glob) and is synced lazily on every search by comparing
file mtimes, so external edits — the daemon's own writes, a text editor, a
future Obsidian vault — are picked up without any watcher machinery. The
corpus is hundreds of small files at most; a full mtime scan per search is
microseconds.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import notes

INDEX_FILENAME = ".wtm-index.sqlite3"

# Private-use characters bracket each hit inside a snippet; the UI escapes
# the snippet as plain text first, then swaps these for real <mark> tags —
# so note content can never smuggle HTML into the page through a snippet.
HL_OPEN = "\ue000"
HL_CLOSE = "\ue001"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    name TEXT PRIMARY KEY,
    mtime REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    name UNINDEXED,
    title,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def _connect(notes_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(notes_dir / INDEX_FILENAME, timeout=5.0)
    db.executescript(_SCHEMA)
    return db


def _plain(md: str) -> str:
    """Markdown → indexable text: drop heading/emphasis markers so snippets
    read as prose instead of `**[0:03:12]** **You:** …` soup."""
    text = re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)
    return text.replace("*", "")


def _sync(db: sqlite3.Connection, notes_dir: Path) -> None:
    on_disk = {p.name: p for p in notes_dir.glob("*.md")}
    indexed = dict(db.execute("SELECT name, mtime FROM files"))

    for name in indexed.keys() - on_disk.keys():
        db.execute("DELETE FROM files WHERE name = ?", (name,))
        db.execute("DELETE FROM notes_fts WHERE name = ?", (name,))

    for name, path in on_disk.items():
        try:
            mtime = path.stat().st_mtime
            if indexed.get(name) == mtime:
                continue
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue  # vanished or unreadable mid-scan: index it next time
        db.execute("DELETE FROM notes_fts WHERE name = ?", (name,))
        db.execute(
            "INSERT INTO notes_fts (name, title, body) VALUES (?, ?, ?)",
            (name, notes.note_title(path), _plain(body)),
        )
        db.execute(
            "INSERT OR REPLACE INTO files (name, mtime) VALUES (?, ?)", (name, mtime)
        )
    db.commit()


def _match_expr(query: str) -> str:
    """Every term quoted (user input is never FTS syntax), implicit AND,
    trailing term prefix-matched so search-as-you-type works."""
    terms = ['"{}"'.format(t.replace('"', '""')) for t in query.split()]
    if terms:
        terms[-1] += "*"
    return " ".join(terms)


def search_notes(notes_dir: Path, query: str, limit: int = 20) -> list[dict]:
    """Ranked matches: [{name, title, modified, snippet}] — snippet hits are
    bracketed by HL_OPEN/HL_CLOSE."""
    query = query.strip()
    if not query or not notes_dir.is_dir():
        return []
    db = _connect(notes_dir)
    try:
        _sync(db, notes_dir)
        rows = db.execute(
            "SELECT name, title, snippet(notes_fts, 2, ?, ?, ' … ', 14) "
            "FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (HL_OPEN, HL_CLOSE, _match_expr(query), limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # defensive: a MATCH expr sqlite still dislikes ≠ a 500
    finally:
        db.close()

    results = []
    for name, title, snippet in rows:
        try:
            mtime = (notes_dir / name).stat().st_mtime
        except OSError:
            continue  # deleted between sync and here
        results.append(
            {
                "name": name,
                "title": title,
                "modified": datetime.fromtimestamp(mtime).isoformat(),
                "snippet": snippet,
            }
        )
    return results
