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
    title: "Daily standup"
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

The UI manages user templates (create/delete) and the per-user preferences in
config.toml's [templates] table: `favorites` and a `default` that applies
when no template is picked (see resolve_template for the full precedence).
Built-ins are read-only from the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from . import notes
from .config import CONFIG_PATH, load_config, save_config

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
    title: str
    path: Path


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
        elif key in ("name", "title", "description"):
            meta[key] = value.strip("\"'")
    return meta


def valid_sections(sections: str) -> bool:
    # The UI's checkbox toggle (notes.toggle_task / _TASK_RE) and the
    # action-item tracker depend on an Action Items section using "- [ ]".
    return "## Action Items" in sections and "- [ ]" in sections


def _parse(path: Path, builtin: bool) -> Template | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    front, body = notes.split_frontmatter(text)
    meta = _parse_frontmatter(front)
    sections = body.strip()
    name = meta.get("name") or path.stem
    if not valid_sections(sections):
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
        title=meta.get("title") or name.replace("-", " ").capitalize(),
        path=path,
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


def resolve_template(explicit: str | None, title: str | None) -> str | None:
    """The template a meeting is summarized with: an explicit pick, else the
    configured default, else a title-based suggestion, else None (= the
    built-in default sections). A configured default that no longer exists
    is skipped, never an error: a stale config must not break recording."""
    if explicit:
        return explicit
    configured = load_config().default_template
    if configured and load_template(configured) is not None:
        return configured
    return suggest_template(title)


MAX_BODY_CHARS = 20_000


def create_template(name: str, description: str, body: str) -> Template:
    """Write a new user template. ValueError for an unusable name or a body
    missing the Action Items invariant; FileExistsError when the name is taken
    (built-ins included — the UI never overrides or edits a built-in)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-")
    if not slug:
        raise ValueError("template name needs at least one letter or digit")
    sections = body.strip()
    if len(sections) > MAX_BODY_CHARS:
        raise ValueError(f"template body is over {MAX_BODY_CHARS} characters")
    if not valid_sections(sections):
        raise ValueError("template body needs a '## Action Items' section with '- [ ]' tasks")
    if load_template(slug) is not None:
        raise FileExistsError(f"template '{slug}' already exists")
    title = " ".join(name.split())
    text = (
        "---\n"
        f"name: {slug}\n"
        f'title: "{title}"\n'
        f'description: "{" ".join(description.split())}"\n'
        "match: []\n"
        "---\n"
        f"{sections}\n"
    )
    USER_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_TEMPLATES_DIR / f"{slug}.md"
    with path.open("x", encoding="utf-8") as fh:  # a stray invalid file still collides
        fh.write(text)
    created = _parse(path, builtin=False)
    if created is None:
        raise OSError(f"could not read back {path}")
    return created


def delete_template(name: str) -> None:
    """Remove a user template. LookupError if unknown, PermissionError for a
    built-in. Drops it from favorites/default unless a built-in of the same
    name (which the user file was shadowing) takes its place."""
    t = load_template(name)
    if t is None:
        raise LookupError(name)
    if t.builtin:
        raise PermissionError(f"'{name}' is a built-in template")
    t.path.unlink(missing_ok=True)
    if load_template(name) is not None:
        return
    cfg = load_config()
    updates: dict[str, str | list[str] | None] = {}
    if name in cfg.favorite_templates:
        updates["favorite_templates"] = [f for f in cfg.favorite_templates if f != name]
    if cfg.default_template == name:
        updates["default_template"] = None
    if updates:
        save_config(updates)


def set_favorite(name: str, favorite: bool) -> None:
    """LookupError if `name` is not a template."""
    if load_template(name) is None:
        raise LookupError(name)
    favorites = [f for f in load_config().favorite_templates if f != name]
    if favorite:
        favorites.append(name)
    save_config({"favorite_templates": favorites})


def set_default(name: str | None) -> None:
    """None clears the configured default. LookupError if `name` is unknown."""
    if name is not None and load_template(name) is None:
        raise LookupError(name)
    save_config({"default_template": name})
