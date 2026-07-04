"""Meeting summarization via a local Ollama model. Nothing leaves the machine."""

from __future__ import annotations

import requests

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"
# Cap the context window: Ollama otherwise uses the model's maximum, which can
# balloon the KV cache to tens of GB and swap the whole machine.
NUM_CTX = 16_384

SYSTEM_PROMPT = """\
You are a meeting-notes assistant. You will receive a raw meeting transcript.
Produce concise, well-organized notes in Markdown with exactly these sections:

## TL;DR
2-3 sentences capturing what the meeting was about and its outcome.

## Key Points
Bulleted list of the important topics discussed.

## Decisions
Bulleted list of decisions that were made. Write "None recorded." if there were none.

## Action Items
Bulleted list in the form "- [ ] task — owner (if mentioned)". Write "None recorded." if there were none.

## Open Questions
Anything left unresolved. Write "None." if nothing.

Lines may be labeled "You:" (the note-taker's own voice) and "Others:"
(the other meeting participants) — use this to attribute action items.
Be faithful to the transcript; never invent names, dates, or commitments.
The transcript may contain speech-recognition errors — use context to interpret them.
"""

# Map-reduce threshold — sized so one pass (~10k tokens) fits inside NUM_CTX
# with room for the system prompt and the generated notes.
MAX_CHARS_PER_PASS = 40_000


class OllamaError(RuntimeError):
    pass


def _chat(model: str, system: str, user: str, timeout: int = 600) -> str:
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "options": {"num_ctx": NUM_CTX},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=timeout,
        )
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


def check_model(model: str = DEFAULT_MODEL) -> bool:
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
    except requests.RequestException:
        return False
    names = [m["name"] for m in resp.json().get("models", [])]
    return any(n == model or n.split(":")[0] == model for n in names)


def summarize(transcript: str, model: str = DEFAULT_MODEL, context: str = "") -> str:
    """Summarize a transcript into structured meeting notes."""
    prefix = f"Context from the organizer: {context}\n\n" if context else ""

    if len(transcript) <= MAX_CHARS_PER_PASS:
        return _chat(model, SYSTEM_PROMPT, prefix + "Transcript:\n\n" + transcript)

    # Map-reduce for very long meetings: summarize windows, then merge.
    parts = [
        transcript[i : i + MAX_CHARS_PER_PASS]
        for i in range(0, len(transcript), MAX_CHARS_PER_PASS)
    ]
    partials = []
    for n, part in enumerate(parts, 1):
        partials.append(
            _chat(
                model,
                SYSTEM_PROMPT,
                f"{prefix}This is part {n} of {len(parts)} of a long transcript:\n\n{part}",
            )
        )
    merged = "\n\n---\n\n".join(partials)
    return _chat(
        model,
        SYSTEM_PROMPT,
        prefix
        + "Merge these per-section notes from consecutive parts of one meeting "
        + "into a single coherent set of notes:\n\n"
        + merged,
    )
