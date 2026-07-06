"""Notion push — the ONE sanctioned exception to "nothing leaves the machine".

Rules (also encoded in CLAUDE.md; never weaken them):
  * Off unless the user pasted a token + database id into config.toml.
  * Pushes exactly one note, only when the user asks (CLI prompt / UI button
    behind a confirmation) — nothing here is ever called automatically.
  * What is sent is the note itself (title, date, attendees, body) to
    api.notion.com and nothing else — no telemetry, no other notes.

Markdown → Notion blocks is a line-based conversion tuned to what save_note
emits: headings, bullet/numbered lists, task items, quotes, dividers,
paragraphs, with **bold** and `code` spans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import requests

from . import notes
from .export import note_date

API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TIMEOUT = 30
MAX_TEXT = 2000       # Notion's per-rich_text content limit
MAX_CHILDREN = 100    # Notion's blocks-per-request limit

_INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TASK_RE = re.compile(r"^[-*+]\s+\[([ xX])\]\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_ATTENDEES_RE = re.compile(r'^attendees:\s*\[(.*)\]\s*$', re.MULTILINE)


class NotionError(RuntimeError):
    pass


def _rich_text(text: str) -> list[dict]:
    """Markdown inline spans → Notion rich_text, chunked under MAX_TEXT."""
    spans: list[dict] = []
    for piece in _INLINE_RE.split(text):
        if not piece:
            continue
        annotations = {}
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            piece, annotations = piece[2:-2], {"bold": True}
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            piece, annotations = piece[1:-1], {"code": True}
        for i in range(0, len(piece), MAX_TEXT):
            span: dict = {"type": "text", "text": {"content": piece[i : i + MAX_TEXT]}}
            if annotations:
                span["annotations"] = annotations
            spans.append(span)
    return spans


def _block(block_type: str, text: str, **extra) -> dict:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": _rich_text(text), **extra},
    }


def markdown_to_blocks(md: str) -> list[dict]:
    """Note body → Notion blocks. The first H1 is skipped — it becomes the
    page title property instead of a duplicated heading."""
    _, body = notes.split_frontmatter(md)
    blocks: list[dict] = []
    seen_h1 = False
    for line in body.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", line.strip()):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue
        if m := _HEADING_RE.match(line):
            level = min(len(m.group(1)), 3)
            if len(m.group(1)) == 1 and not seen_h1:
                seen_h1 = True
                continue
            blocks.append(_block(f"heading_{level}", m.group(2)))
            continue
        if m := _TASK_RE.match(line):
            blocks.append(_block("to_do", m.group(2), checked=m.group(1) in "xX"))
            continue
        if m := _BULLET_RE.match(line):
            blocks.append(_block("bulleted_list_item", m.group(1)))
            continue
        if m := _NUMBERED_RE.match(line):
            blocks.append(_block("numbered_list_item", m.group(1)))
            continue
        if line.startswith("> "):
            blocks.append(_block("quote", line[2:]))
            continue
        blocks.append(_block("paragraph", line))
    return blocks


def _frontmatter_attendees(md: str) -> list[str]:
    """Attendees back out of our own frontmatter format (quoted YAML list)."""
    fm, _ = notes.split_frontmatter(md)
    if not fm:
        return []
    m = _ATTENDEES_RE.search(fm)
    if not m:
        return []
    return [a.replace('\\"', '"').replace("\\\\", "\\") for a in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))]


@dataclass
class PushPreview:
    """Exactly what a push would send — shown to the user before confirming."""

    title: str
    date: str
    attendees: list[str]
    block_count: int
    char_count: int
    database_id: str


def preview_push(path: Path, database_id: str) -> PushPreview:
    md = path.read_text(encoding="utf-8")
    return PushPreview(
        title=notes.note_title(path),
        date=note_date(path).strftime("%Y-%m-%dT%H:%M:%S"),
        attendees=_frontmatter_attendees(md),
        block_count=len(markdown_to_blocks(md)),
        char_count=len(md),
        database_id=database_id,
    )


def _request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    try:
        resp = requests.request(
            method,
            url,
            json=payload,
            timeout=TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
    except requests.RequestException as exc:
        raise NotionError(f"Could not reach api.notion.com: {exc}") from exc
    if not resp.ok:
        try:
            message = resp.json().get("message", resp.text[:300])
        except ValueError:
            message = resp.text[:300]
        raise NotionError(f"Notion API error ({resp.status_code}): {message}")
    return resp.json()


def _database_properties(token: str, database_id: str) -> tuple[str, str | None, str | None]:
    """(title property name, date property name, attendees multi_select name)
    from the target database's schema — property names vary per database."""
    db = _request("GET", f"{API_URL}/databases/{database_id}", token)
    props = db.get("properties", {})
    title_prop = next((n for n, p in props.items() if p.get("type") == "title"), None)
    if title_prop is None:
        raise NotionError("Target database has no title property.")
    date_prop = next((n for n, p in props.items() if p.get("type") == "date"), None)
    attendees_prop = next(
        (
            n
            for n, p in props.items()
            if p.get("type") == "multi_select" and n.lower() == "attendees"
        ),
        None,
    )
    return title_prop, date_prop, attendees_prop


def push_note(path: Path, token: str, database_id: str) -> str:
    """Create one Notion page from the note; returns the page URL.
    User-initiated only — see the module docstring."""
    md = path.read_text(encoding="utf-8")
    blocks = markdown_to_blocks(md)
    title_prop, date_prop, attendees_prop = _database_properties(token, database_id)

    properties: dict = {
        title_prop: {"title": _rich_text(notes.note_title(path))},
    }
    if date_prop:
        properties[date_prop] = {
            "date": {"start": note_date(path).strftime("%Y-%m-%dT%H:%M:%S")}
        }
    attendees = _frontmatter_attendees(md)
    if attendees_prop and attendees:
        properties[attendees_prop] = {
            # Notion multi_select option names cannot contain commas.
            "multi_select": [{"name": a.replace(",", " ")[:100]} for a in attendees]
        }

    page = _request(
        "POST",
        f"{API_URL}/pages",
        token,
        {
            "parent": {"database_id": database_id},
            "properties": properties,
            "children": blocks[:MAX_CHILDREN],
        },
    )
    for start in range(MAX_CHILDREN, len(blocks), MAX_CHILDREN):
        _request(
            "PATCH",
            f"{API_URL}/blocks/{page['id']}/children",
            token,
            {"children": blocks[start : start + MAX_CHILDREN]},
        )
    return page.get("url", "")
