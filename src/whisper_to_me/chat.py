"""Chat over the notes corpus — local RAG. Nothing leaves the machine.

Retrieval reuses the FTS5 index (search.py); the top notes' summaries plus the
transcript lines that mention the question terms become numbered sources, and a
single local Ollama call answers with `[n]` citations pointing back at them.

The model call goes through summarize._chat (which carries the num_ctx cap), so
prior conversation turns are folded into the user prompt rather than sent as a
separate message array — a single system+user helper can't take a message list.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import notes, search
from . import summarize as summ

SOURCE_BUDGET = 24_000  # total chars of source material per question
PER_SOURCE_CAP = 6_000
MAX_SOURCES = 6
MAX_HISTORY = 6  # prior turns folded into the prompt

CHAT_SYSTEM = """\
You answer questions about the user's own past meetings, using only the
numbered sources provided. Rules:
- Use only facts stated in the sources; never invent, assume, or use outside
  knowledge.
- After each claim, cite the source it came from like [2]. Cite every claim.
- If the sources do not answer the question, say so plainly instead of guessing.
- Be concise. Markdown is fine.
"""

NO_MATCH = "I couldn't find anything about that in your notes."


def _terms(question: str) -> list[str]:
    """Question words worth matching on: drop punctuation and words < 3 chars
    (too noisy to locate transcript lines by)."""
    return [t for t in re.sub(r"[^\w\s]", " ", question.lower()).split() if len(t) >= 3]


def _note_date(front: str | None, path: Path) -> str:
    if front:
        m = re.search(r"^date:\s*(.+)$", front, flags=re.MULTILINE)
        if m:
            return m.group(1).strip()
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return ""


def _matching_lines(transcript: str, terms: list[str]) -> str:
    """Transcript lines mentioning any term, each with one line of context
    on either side (dedup'd, in order)."""
    if not terms:
        return ""
    lines = transcript.splitlines()
    keep: set[int] = set()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(term in low for term in terms):
            keep.update((i - 1, i, i + 1))
    kept = [lines[i] for i in sorted(keep) if 0 <= i < len(lines)]
    return "\n".join(kept).strip()


def _source_block(n: int, notes_dir: Path, name: str, title: str, terms: list[str]) -> str:
    """One numbered source: the note's summary (always) plus transcript lines
    that hit the question terms, clamped to PER_SOURCE_CAP."""
    try:
        raw = (notes_dir / name).read_text(encoding="utf-8")
    except OSError:
        return ""
    front, body = notes.split_frontmatter(raw)
    cut = re.search(r"^## Transcript\s*$", body, flags=re.MULTILINE)
    summary = (body[: cut.start()] if cut else body).strip()
    excerpts = _matching_lines(body[cut.end() :], terms) if cut else ""
    content = summary
    if excerpts:
        content += "\n\nTranscript excerpts:\n" + excerpts
    content = content[:PER_SOURCE_CAP].strip()
    date = _note_date(front, notes_dir / name)
    return f'[{n}] "{title}" ({date})\n{content}'


def _history_text(history: list[dict] | None) -> str:
    if not history:
        return ""
    turns = []
    for turn in history[-MAX_HISTORY:]:
        role = turn.get("role") if isinstance(turn, dict) else None
        content = str(turn.get("content", "")).strip() if isinstance(turn, dict) else ""
        if role not in ("user", "assistant") or not content:
            continue
        turns.append(f"{'User' if role == 'user' else 'Assistant'}: {content}")
    if not turns:
        return ""
    return "Earlier in this conversation:\n" + "\n".join(turns) + "\n\n"


def answer_question(
    notes_dir: Path,
    question: str,
    model: str = summ.DEFAULT_MODEL,
    history: list[dict] | None = None,
) -> dict:
    """Answer `question` from the notes. Returns {"answer", "sources"} where
    sources are only the notes actually cited in the answer. No hits → a canned
    reply with no Ollama call. OllamaError propagates to the caller."""
    terms = _terms(question)
    # OR-match the salient words: a natural-language question must not require
    # every word ("when", "does", "who") to appear in a note (that ANDs to
    # nothing). Fall back to the raw question when it has no long-enough terms.
    hits = search.search_notes(
        notes_dir, " ".join(terms) or question, limit=MAX_SOURCES, match_all=False
    )
    if not hits:
        return {"answer": NO_MATCH, "sources": []}

    blocks: list[str] = []
    used: list[dict] = []
    total = 0
    for hit in hits:
        n = len(used) + 1
        block = _source_block(n, notes_dir, hit["name"], hit["title"], terms)
        if not block:
            continue
        if blocks and total + len(block) > SOURCE_BUDGET:
            break
        blocks.append(block)
        used.append({"n": n, "name": hit["name"], "title": hit["title"]})
        total += len(block)
    if not blocks:  # every hit unreadable — don't ask Ollama about nothing
        return {"answer": NO_MATCH, "sources": []}

    user = (
        _history_text(history)
        + "Sources:\n\n"
        + "\n\n".join(blocks)
        + f"\n\nQuestion: {question}"
    )
    answer = summ._chat(model, CHAT_SYSTEM, user)
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    sources = [s for s in used if s["n"] in cited]
    return {"answer": answer, "sources": sources}
