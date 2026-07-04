---
description: Run the no-mic end-to-end pipeline test (transcribe + summarize + note)
---

Run the end-to-end pipeline test that does NOT touch the microphone:

1. Generate test audio:
   `say -o /tmp/wtm-e2e.aiff "Sprint planning. We decided to ship on Friday. Sara owns the migration script. Open question: do we need security approval?"`
   then `afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/wtm-e2e.aiff /tmp/wtm-e2e.wav`
2. Ensure Ollama is up (`curl -s localhost:11434/api/tags`); if not, start it
   (`open -a Ollama` or `ollama serve` in background) and wait a few seconds.
3. Run: `uv run wtm transcribe /tmp/wtm-e2e.wav --title "E2E test" --language en`
4. Verify: transcript lines match the spoken text, the summary has TL;DR /
   Key Points / Decisions / Action Items / Open Questions sections, and a note
   file was written under ~/MeetingNotes.
5. If asked to also test the system-audio tap: hold its stdin open
   (`sleep 9 | ~/.cache/whisper-to-me/system-audio-tap > /tmp/tap.raw`) while
   playing audio (works even with output muted), then check /tmp/tap.raw is
   non-empty float32 and transcribes correctly.

Report pass/fail per step with the actual transcript and summary quality.
Clean up /tmp/wtm-e2e.* afterwards.
