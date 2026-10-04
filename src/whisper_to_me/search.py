"""Full-text search over the notes directory — SQLite FTS5, one local file.

The index lives inside the notes directory (`.wtm-index.sqlite3`, hidden and
outside the `*.md` glob) and is synced lazily on every search by comparing
file mtimes, so external edits — the daemon's own writes, a text editor, a
future Obsidian vault — are picked up without any watcher machinery. The
corpus is hundreds of small files at most; a full mtime scan per search is
microseconds.

Two FTS tables: `notes_fts` (one row per note) decides which notes match —
AND for the sidebar, OR for chat/briefs — and ranks them; `hits_fts` (one row
per title, summary, and transcript line) supplies the snippets, so a sidebar
hit can seek to the exact second it was said. Both are built from the same
rows, so every note-level match has at least one hit row.

The index is a cache: bumping SCHEMA_VERSION drops and rebuilds it.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import notes

INDEX_FILENAME = ".wtm-index.sqlite3"
SCHEMA_VERSION = 3

# Private-use characters bracket each hit inside a snippet; the UI escapes
# the snippet as plain text first, then swaps these for real <mark> tags —
# so note content can never smuggle HTML into the page through a snippet.
HL_OPEN = ""
HL_CLOSE = ""

HITS_PER_NOTE = 3
TITLE_WEIGHT = 5.0
# Relevance is scaled by 0.5..1.0 with this half-life, so recency breaks ties
# and nudges close calls but never buries a much better match.
RECENCY_HALF_LIFE_DAYS = 60.0

_TABLES = """
CREATE TABLE files (
    name TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    title TEXT NOT NULL,
    date TEXT,
    app TEXT,
    ts REAL NOT NULL
);
CREATE VIRTUAL TABLE notes_fts USING fts5(
    name UNINDEXED,
    title,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE hits_fts USING fts5(
    name UNINDEXED,
    kind UNINDEXED,
    t UNINDEXED,
    speaker UNINDEXED,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_TRANSCRIPT_RE = re.compile(r"^## Transcript\s*$", flags=re.MULTILINE)


@dataclass(frozen=True)
class _Row:
    kind: str  # "title" | "summary" | "line"
    t: int | None
    speaker: str | None
    text: str


def _connect(notes_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(notes_dir / INDEX_FILENAME, timeout=5.0)
    db.execute("BEGIN IMMEDIATE")  # two first searches must not both rebuild
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        for table in ("files", "notes_fts", "hits_fts"):
            db.execute(f"DROP TABLE IF EXISTS {table}")
        for stmt in _TABLES.split(";"):
            if stmt.strip():
                db.execute(stmt)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.commit()
    return db


def _plain(md: str) -> str:
    """Markdown → indexable text: heading/emphasis markers dropped so snippets
    read as prose instead of `## **TL;DR**` soup."""
    text = re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)
    return text.replace("*", "")


def _rows(title: str, body: str) -> list[_Row]:
    """A note body (frontmatter already split off) → its hit rows: the title,
    the summary (everything above `## Transcript`, minus the H1), and one row
    per transcript line with its stamp in seconds and its speaker label."""
    rows = [_Row("title", None, None, title)]
    cut = _TRANSCRIPT_RE.search(body)
    summary = body[: cut.start()] if cut else body
    summary = re.sub(r"^# .*$", "", summary, count=1, flags=re.MULTILINE)
    summary = _plain(summary).strip()
    if summary:
        rows.append(_Row("summary", None, None, summary))
    for line in notes.parse_transcript(body):
        text = line.text.replace("*", "").strip()
        if text and text != "(empty)":
            rows.append(_Row("line", line.t, line.speaker, text))
    return rows


def _sync(db: sqlite3.Connection, notes_dir: Path) -> None:
    # One writer at a time: a concurrent sync would otherwise index the same
    # changed note twice and every hit would show up doubled.
    db.execute("BEGIN IMMEDIATE")
    on_disk = {p.name: p for p in notes_dir.glob("*.md")}
    indexed = dict(db.execute("SELECT name, mtime FROM files"))

    for name in indexed.keys() - on_disk.keys():
        for table in ("files", "notes_fts", "hits_fts"):
            db.execute(f"DELETE FROM {table} WHERE name = ?", (name,))

    for name, path in on_disk.items():
        try:
            mtime = path.stat().st_mtime
            if indexed.get(name) == mtime:
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue  # vanished or unreadable mid-scan: index it next time
        front, body = notes.split_frontmatter(text)
        title = notes.note_title(path)
        date = notes.meeting_date(text)
        ts = datetime.fromisoformat(date).timestamp() if date else mtime
        rows = _rows(title, body)
        db.execute("DELETE FROM notes_fts WHERE name = ?", (name,))
        db.execute("DELETE FROM hits_fts WHERE name = ?", (name,))
        db.execute(
            "INSERT INTO notes_fts (name, title, body) VALUES (?, ?, ?)",
            (name, title, "\n".join(r.text for r in rows if r.kind != "title")),
        )
        db.executemany(
            "INSERT INTO hits_fts (name, kind, t, speaker, text) VALUES (?, ?, ?, ?, ?)",
            [(name, r.kind, r.t, r.speaker, r.text) for r in rows],
        )
        db.execute(
            "INSERT OR REPLACE INTO files (name, mtime, title, date, app, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, mtime, title, date, notes.frontmatter_fields(text).get("app") or None, ts),
        )
    db.commit()


def _match_expr(query: str, match_all: bool = True) -> str:
    """Every term quoted (user input is never FTS syntax) and prefix-matched
    when it is ≥3 chars or the last one (still being typed). `match_all` (the
    sidebar default) ANDs the terms; match_all=False ORs them, which is what
    chat retrieval wants — a natural-language question shouldn't require every
    word to appear, just the salient ones to rank a note up."""
    words = query.split()
    terms = []
    for i, word in enumerate(words):
        term = '"{}"'.format(word.replace('"', '""'))
        if len(word) >= 3 or i == len(words) - 1:
            term += "*"
        terms.append(term)
    return (" " if match_all else " OR ").join(terms)


def _recency(ts: float, now: float) -> float:
    age_days = max(0.0, now - ts) / 86400
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def search_notes(
    notes_dir: Path, query: str, limit: int = 20, match_all: bool = True
) -> list[dict]:
    """Ranked note matches: [{name, title, modified, date, app, snippet}] —
    snippet hits are bracketed by HL_OPEN/HL_CLOSE. Ranked by bm25 (title
    weighted) scaled by recency."""
    query = query.strip()
    if not query or not notes_dir.is_dir():
        return []
    db = _connect(notes_dir)
    try:
        _sync(db, notes_dir)
        rows = db.execute(
            "SELECT n.name, f.title, f.date, f.app, f.ts, bm25(notes_fts, 0, ?, 1.0), "
            "snippet(notes_fts, 2, ?, ?, ' … ', 14) "
            "FROM notes_fts AS n JOIN files AS f ON f.name = n.name "
            "WHERE notes_fts MATCH ?",
            (TITLE_WEIGHT, HL_OPEN, HL_CLOSE, _match_expr(query, match_all)),
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # defensive: a MATCH expr sqlite still dislikes ≠ a 500
    finally:
        db.close()

    now = time.time()
    # bm25() is negative, more negative = better; flip it so higher wins.
    rows.sort(key=lambda r: -r[5] * (0.5 + 0.5 * _recency(r[4], now)), reverse=True)
    results = []
    for name, title, date, app, _ts, _rank, snippet in rows[:limit]:
        try:
            mtime = (notes_dir / name).stat().st_mtime
        except OSError:
            continue  # deleted between sync and here
        results.append(
            {
                "name": name,
                "title": title,
                "modified": datetime.fromtimestamp(mtime).isoformat(),
                "date": date,
                "app": app,
                "snippet": snippet,
            }
        )
    return results


_KIND_ORDER = {"title": 0, "summary": 1, "line": 2}


def search(notes_dir: Path, query: str, limit: int = 20) -> list[dict]:
    """The sidebar search (GET /api/search): notes matching every term (AND),
    each with up to HITS_PER_NOTE hits — [{name, title, date, app, hits:
    [{kind, t, speaker, snippet}]}]. A line hit's `t` is seconds from the
    meeting start, so the UI can seek straight to it."""
    found = search_notes(notes_dir, query, limit=limit, match_all=True)
    if not found:
        return []
    # A hit row only needs one of the terms: the AND already held note-wide.
    any_term = _match_expr(query.strip(), match_all=False)
    db = _connect(notes_dir)
    try:
        results = []
        for note in found:
            hits = db.execute(
                "SELECT kind, t, speaker, snippet(hits_fts, 4, ?, ?, ' … ', 12) "
                "FROM hits_fts WHERE hits_fts MATCH ? AND name = ? "
                "ORDER BY rank LIMIT ?",
                (HL_OPEN, HL_CLOSE, any_term, note["name"], HITS_PER_NOTE),
            ).fetchall()
            hits.sort(key=lambda h: (_KIND_ORDER[h[0]], h[1] if h[1] is not None else -1))
            results.append(
                {
                    "name": note["name"],
                    "title": note["title"],
                    "date": note["date"],
                    "app": note["app"],
                    "hits": [
                        {"kind": k, "t": t, "speaker": sp, "snippet": snip}
                        for k, t, sp, snip in hits
                    ],
                }
            )
        return results
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
