"""Meeting templates — per-type synthesis section blocks, auto-suggested from
the meeting title.

Built-in templates ship with the package (`templates/*.md`); a user can add or
override any of them by dropping a markdown file in
`~/.config/whisper-to-me/templates/`. Read fresh on every use (no caching, no
restart needed — like config.py). A malformed or invalid template file is
skipped with a warning, never a crash: a typo must not take recording down.

File format is YAML-ish frontmatter + a section-block body:

    ---
    name: standup
    description: "Daily standup — per-person updates and blockers"
    match: [standup, stand-up, daily, scrum]
    ---
    ## TL;DR
    ...
    ## Action Items
    - [ ] task — owner (due date)
    ...

The body replaces the default synthesis sections; the header and the
faithfulness rules around it stay fixed (see summarize._synth_system).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from . import notes
from .config import CONFIG_PATH

console = Console()

BUILTIN_DIR = Path(__file__).with_name("templates")
USER_TEMPLATES_DIR = CONFIG_PATH.parent / "templates"


@dataclass(frozen=True)
class Template:
    name: str
    description: str
    match: tuple[str, ...]
    sections: str
    builtin: bool


def _parse_frontmatter(front: str | None) -> dict:
    """Minimal, tolerant parse of the three keys we use — no YAML dependency."""
    meta: dict = {}
    if not front:
        return meta
    for raw in front.splitlines():
        line = raw.strip()
        if not line or line == "---" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "match":
            meta["match"] = [
                t.strip().strip("\"'").lower()
                for t in value.strip("[]").split(",")
                if t.strip()
            ]
        elif key in ("name", "description"):
            meta[key] = value.strip("\"'")
    return meta


def _parse(path: Path, builtin: bool) -> Template | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    front, body = notes.split_frontmatter(text)
    meta = _parse_frontmatter(front)
    sections = body.strip()
    name = meta.get("name") or path.stem
    # The UI's checkbox toggle (notes.toggle_task / _TASK_RE) and the
    # action-item tracker depend on an Action Items section using "- [ ]".
    if "## Action Items" not in sections or "- [ ]" not in sections:
        console.print(
            f"[yellow]Skipping template '{name}' ({path}): needs a "
            "'## Action Items' section with '- [ ]' tasks.[/yellow]"
        )
        return None
    return Template(
        name=name,
        description=meta.get("description", ""),
        match=tuple(meta.get("match", ())),
        sections=sections,
        builtin=builtin,
    )


def list_templates() -> list[Template]:
    """Built-ins plus user files; a user file shadows a built-in of the same
    name. Deterministic order: built-ins (by filename), then user-only ones."""
    found: dict[str, Template] = {}
    for path in sorted(BUILTIN_DIR.glob("*.md")):
        t = _parse(path, builtin=True)
        if t is not None:
            found[t.name] = t
    if USER_TEMPLATES_DIR.is_dir():
        for path in sorted(USER_TEMPLATES_DIR.glob("*.md")):
            t = _parse(path, builtin=False)
            if t is not None:
                found[t.name] = t
    return list(found.values())


def load_template(name: str) -> Template | None:
    return next((t for t in list_templates() if t.name == name), None)


def suggest_template(title: str | None) -> str | None:
    """First template whose match terms appear (as substrings) in the title;
    None for no title or no hit. The default template has no match terms, so
    it is never auto-suggested."""
    if not title:
        return None
    low = title.lower()
    for t in list_templates():
        if any(term in low for term in t.match):
            return t.name
    return None
