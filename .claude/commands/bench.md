---
description: Benchmark Whisper real-time factor (RTF) on this machine
---

Benchmark transcription speed so model-choice decisions stay evidence-based:

1. Generate ~25s of test audio with `say` + `afconvert` (16 kHz mono WAV).
2. For each model in [$ARGUMENTS or default: large-v3-turbo, small]:
   time `WhisperModel(name, device="auto", compute_type="int8")
   .transcribe(audio, vad_filter=True, beam_size=5, language="en")` via
   `uv run python`, and compute RTF = elapsed / audio_duration.
3. Report a table: model, load time, transcribe time, RTF, and a one-line
   transcript-quality note. RTF must stay well below 0.5 for live use with
   two concurrent sources (mic + system tap).
