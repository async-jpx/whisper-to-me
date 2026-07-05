"""Meeting summarization via a local Ollama model. Nothing leaves the machine.

Pipeline (all local):
  1. Window the transcript on line boundaries, with overlap.
  2. Per window: structured JSON fact extraction (decisions, action items,
     risks, open questions…) — an easier task for a small model than freeform
     summarizing, and the results merge deterministically.
  3. Merge the facts in Python (fuzzy dedupe across windows).
  4. One synthesis call turns the merged facts into the final Markdown notes,
     so depth no longer degrades with meeting length.
  5. One small call names the meeting (used when the user gave no title).
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

import requests

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"
# Cap the context window: Ollama otherwise uses the model's maximum, which can
# balloon the KV cache to tens of GB and swap the whole machine.
NUM_CTX = 16_384

WINDOW_CHARS = 24_000  # ~6k tokens: window + prompts + JSON reply fit NUM_CTX
OVERLAP_CHARS = 2_000  # tail of one window repeats at the start of the next
SIMILAR_RATIO = 0.82   # fuzzy-dedupe threshold when merging across windows

LIST_KEYS = ("topics", "key_points", "decisions", "risks", "open_questions")

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["task", "owner", "due"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["purpose", *LIST_KEYS, "action_items"],
}

TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}

EXTRACT_SYSTEM = """\
You extract facts from an excerpt of a meeting transcript. Respond with JSON only.
- purpose: one sentence on what this part of the meeting is trying to achieve ("" if unclear).
- topics: short noun phrases for the subjects discussed.
- key_points: the substantive things said or reported, one short sentence each.
- decisions: only decisions explicitly made in the text.
- action_items: concrete tasks somebody committed to; owner/due "" unless stated.
- risks: risks, blockers, or concerns raised.
- open_questions: questions raised but not answered in the text.
Empty arrays and "" are correct when the excerpt has nothing to report — never
invent facts, names, or dates. Lines may be labeled **You:** (the note-taker)
and **Others:** (the other participants) — use that to attribute action items.
The transcript may contain speech-recognition errors; interpret them by context.
"""

SYNTH_SYSTEM = """\
You are a meeting-notes writer. You will receive structured facts extracted
from the full transcript of one meeting. Write concise, actionable Markdown
notes with exactly these sections:

## TL;DR
2-3 sentences: what the meeting was for and what came out of it.

## Decisions
Bulleted list.

## Action Items
Bullets in the form "- [ ] task — owner (due date)"; omit owner/due when not
given.

## Risks & Blockers
Bulleted list.

## Open Questions
Bulleted list.

## Discussion
The key points grouped by topic: a short bold topic label, then its bullets.

A section with no facts gets exactly one line — "None recorded." — and
nothing else; never mix bullets with a "None" line. Be faithful to the given
facts: merge near-duplicates, drop filler, never invent names, dates, or
commitments. Keep every bullet short and concrete.
"""

TITLE_SYSTEM = """\
Name this meeting from the facts given. Respond with JSON only: a specific,
concrete title of 3 to 7 words — the kind a person would give a calendar
event. No dates, no quotes, no generic titles like "Team Meeting".
"""


class OllamaError(RuntimeError):
    pass


def _chat(
    model: str, system: str, user: str, timeout: int = 600, schema: dict | None = None
) -> str:
    payload = {
        "model": model,
        "stream": False,
        "options": {"num_ctx": NUM_CTX},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if schema is not None:
        payload["format"] = schema
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.ConnectionError as exc:
        raise OllamaError(
            "Cannot reach Ollama at localhost:11434 — is `ollama serve` running?"
        ) from exc
    except requests.Timeout as exc:
        raise OllamaError(
            f"Ollama took longer than {timeout}s — the model '{model}' may be "
            "too large for this machine; try a smaller one (e.g. llama3.2:3b)."
        ) from exc
    except requests.HTTPError as exc:
        raise OllamaError(f"Ollama error: {exc.response.text[:300]}") from exc
    return resp.json()["message"]["content"].strip()


def _chat_json(model: str, system: str, user: str, schema: dict) -> dict:
    last_error = None
    for _ in range(2):
        try:
            return json.loads(_chat(model, system, user, schema=schema))
        except json.JSONDecodeError as exc:
            last_error = exc
    raise OllamaError(f"Model '{model}' did not return valid JSON: {last_error}")


def check_model(model: str = DEFAULT_MODEL) -> bool:
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
    except requests.RequestException:
        return False
    names = [m["name"] for m in resp.json().get("models", [])]
    return any(n == model or n.split(":")[0] == model for n in names)


def _windows(transcript: str) -> list[str]:
    """Split on line boundaries into overlapping windows of ~WINDOW_CHARS."""
    lines: list[str] = []
    for line in transcript.splitlines():
        while len(line) > WINDOW_CHARS:  # pathological unbroken text
            lines.append(line[:WINDOW_CHARS])
            line = line[WINDOW_CHARS:]
        lines.append(line)

    windows: list[str] = []
    current: list[str] = []
    size = 0
    fresh = False  # any line added since the last flush (vs. pure overlap)
    for line in lines:
        current.append(line)
        size += len(line) + 1
        fresh = True
        if size >= WINDOW_CHARS:
            windows.append("\n".join(current))
            tail: list[str] = []
            tail_size = 0
            for prev in reversed(current):
                if tail_size >= OVERLAP_CHARS:
                    break
                tail.insert(0, prev)
                tail_size += len(prev) + 1
            current, size, fresh = tail, tail_size, False
    if fresh or not windows:
        windows.append("\n".join(current))
    return windows


def _dedupe(items: list[str]) -> list[str]:
    kept: list[str] = []
    kept_norm: list[str] = []
    for item in items:
        norm = " ".join(str(item).lower().split())
        if not norm:
            continue
        if any(SequenceMatcher(None, norm, k).ratio() >= SIMILAR_RATIO for k in kept_norm):
            continue
        kept.append(str(item).strip())
        kept_norm.append(norm)
    return kept


def _merge_facts(per_window: list[dict]) -> dict:
    merged: dict = {
        "purpose": _dedupe([f.get("purpose", "") for f in per_window]),
        "action_items": [],
    }
    for key in LIST_KEYS:
        merged[key] = _dedupe([x for f in per_window for x in (f.get(key) or [])])

    for facts in per_window:
        for item in facts.get("action_items") or []:
            if isinstance(item, str):
                item = {"task": item, "owner": "", "due": ""}
            task = " ".join(str(item.get("task", "")).lower().split())
            if not task:
                continue
            match = next(
                (
                    kept
                    for kept in merged["action_items"]
                    if SequenceMatcher(
                        None, task, " ".join(kept["task"].lower().split())
                    ).ratio()
                    >= SIMILAR_RATIO
                ),
                None,
            )
            if match is None:
                merged["action_items"].append(
                    {
                        "task": str(item.get("task", "")).strip(),
                        "owner": str(item.get("owner", "")).strip(),
                        "due": str(item.get("due", "")).strip(),
                    }
                )
            else:  # duplicate: keep it, but fill in owner/due if this one knows more
                match["owner"] = match["owner"] or str(item.get("owner", "")).strip()
                match["due"] = match["due"] or str(item.get("due", "")).strip()
    return merged


def _extract(window: str, n: int, total: int, model: str, context: str) -> dict:
    prefix = f"Context from the organizer: {context}\n\n" if context else ""
    part = f"part {n} of {total} of the transcript" if total > 1 else "the transcript"
    return _chat_json(
        model, EXTRACT_SYSTEM, f"{prefix}This is {part}:\n\n{window}", EXTRACT_SCHEMA
    )


def infer_title(facts: dict, model: str = DEFAULT_MODEL) -> str | None:
    """A short meeting title from merged facts; None if nothing usable."""
    seed = {k: facts.get(k) for k in ("purpose", "topics", "decisions")}
    data = _chat_json(
        model, TITLE_SYSTEM, json.dumps(seed, ensure_ascii=False), TITLE_SCHEMA
    )
    title = re.sub(r"\s+", " ", str(data.get("title", ""))).strip(" \"'")
    return title[:70] or None


def summarize_meeting(
    transcript: str, model: str = DEFAULT_MODEL, context: str = ""
) -> tuple[str, str | None]:
    """Full pipeline: returns (markdown notes, inferred title or None)."""
    windows = _windows(transcript)
    facts = _merge_facts(
        [_extract(w, n, len(windows), model, context) for n, w in enumerate(windows, 1)]
    )
    prefix = f"Context from the organizer: {context}\n\n" if context else ""
    notes_md = _chat(
        model,
        SYNTH_SYSTEM,
        prefix
        + "Structured facts extracted from the meeting transcript:\n\n"
        + json.dumps(facts, indent=2, ensure_ascii=False),
    )
    try:
        title = infer_title(facts, model)
    except OllamaError:
        title = None  # a note without an inferred title is still a good note
    return notes_md, title
