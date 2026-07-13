"""Test bootstrap: none of the unit/API tests touch real audio or meeting
detection, but importing `whisper_to_me.server` pulls in `watch`, which
dlopens CoreAudio at import time. On macOS that just works; anywhere else
(CI, containers) substitute an inert stub so the rest of the suite runs.
"""

from __future__ import annotations

import sys
import types

try:
    import whisper_to_me.watch  # noqa: F401  (macOS: the real module loads)
except OSError:  # no CoreAudio: not macOS
    stub = types.ModuleType("whisper_to_me.watch")
    stub.detect_meeting = lambda: None
    stub.meeting_title_hint = lambda trigger: None
    stub.zoom_meeting_active = lambda: False
    stub.mic_in_use_by_others = lambda exclude_pids=frozenset(): None
    stub.notify = lambda title, message: None
    sys.modules["whisper_to_me.watch"] = stub
