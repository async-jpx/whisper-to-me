# whisper-to-me

A local, private clone of Notion AI Meeting Notes: it listens to your meetings,
live-transcribes them, and writes structured Markdown notes with an AI summary —
**entirely on your machine**. No audio, transcript, or summary ever leaves it.

## How Notion does it (and how this maps)

Notion AI Meeting Notes is bot-free: instead of joining calls with a bot, its
desktop app captures microphone + system audio at the device level, streams it
to cloud ASR for real-time transcription, then a cloud LLM produces a summary
with key points, decisions, and action items, saved as a Notion page.

| Notion layer | whisper-to-me (all local) |
|---|---|
| Device-level mic/system audio capture | PortAudio via `sounddevice`, energy-VAD utterance chunking |
| Cloud streaming ASR | `faster-whisper` (offline after one model download) |
| Cloud LLM summarization | Ollama (`llama3.2:3b` by default) |
| Notion page | Markdown note in `~/MeetingNotes` |

## Requirements

- macOS, [Ollama](https://ollama.com) running, and `uv`
- `ollama pull llama3.2:3b` (the default summarizer, ~2 GB)

## Usage

```sh
uv run wtm devices                        # list input devices
uv run wtm record --title "Team sync"     # live-transcribe; Ctrl-C to stop & summarize
uv run wtm watch                          # Notion-style: auto-detects meetings & takes notes
uv run wtm transcribe recording.wav       # transcribe + summarize an audio file
uv run wtm summarize transcript.md        # (re)summarize existing text
```

Useful flags: `--model small|medium|large-v3` (Whisper size), `--language en`,
`--ollama-model NAME`, `--context "attendees, agenda hints"`, `--no-summary`,
`--notes-dir PATH`, and `--device N` for `record`.

First run downloads the Whisper model once; everything afterwards is offline.
macOS will ask for microphone permission for your terminal on first recording.

## Capturing remote meetings (both sides of the call)

Like Notion, this captures at the device level. Your mic only hears you and
room audio. To also capture remote participants:

Automatic. Alongside your mic (**You:**), a native ScreenCaptureKit tap
(compiled on first run from `system_audio_tap.swift`, needs Xcode Command Line
Tools) captures the audio of **every app** — Zoom, Teams, Meet in a browser,
anything (**Others:**) — even while your speakers are muted or you're on
headphones. Lines from both sources are merged by time.

- First run asks once for **System Audio Recording** permission for your
  terminal; approve it and restart the terminal app.
- `--system-device off` disables the tap; `--system-device N` forces a
  specific input device instead. If the tap can't be built, loopback devices
  (BlackHole etc.) are used as fallback.
- Transcript lines are journaled to the note file *as they are spoken*, so a
  crash or kill never loses the transcript; the summary is added at the end.
- If audio plays through speakers (no headphones), the mic hears it too and
  lines can appear under both You and Others — harmless, and the summarizer
  copes.

## Performance notes

Summarization context is capped (`num_ctx=16384`) — without a cap Ollama uses
the model's maximum context, which can balloon the KV cache to tens of GB and
swap the machine. Long meetings are summarized map-reduce style in ~40k-char
windows. Prefer small summarizer models (3–8 B); meeting-note quality does not
need a 24 B coding model.
