"""Follow-up email drafts from a saved note — local Ollama, clipboard-bound.
The draft is returned to the caller; nothing is ever sent anywhere.
"""

from __future__ import annotations

import re

from . import notes
from . import summarize as summ

MAX_CHARS = 20_000

FOLLOWUP_SYSTEM = """\
You write a follow-up email from the note-taker to the other meeting attendees,
based only on the meeting notes provided. Format:
- The first line must be "Subject: ..." — a short, specific subject.
- Then a brief, warm recap of what the meeting covered.
- Then the decisions made.
- Then the action items, each with its owner and due date when known.
- Then a friendly closing line.
Use only facts from the notes — never invent recipients, dates, or commitments.
Plain text only, no markdown.
"""


def draft_followup(note_md: str, model: str = summ.DEFAULT_MODEL) -> str:
    """Draft a follow-up email from a note's markdown. The transcript is
    dropped (it's noise for this and blows the budget); the summary sections
    are the material. OllamaError propagates to the caller."""
    _, body = notes.split_frontmatter(note_md)
    cut = re.search(r"^## Transcript\s*$", body, flags=re.MULTILINE)
    material = (body[: cut.start()] if cut else body).strip()[:MAX_CHARS]
    return summ._chat(model, FOLLOWUP_SYSTEM, material)
