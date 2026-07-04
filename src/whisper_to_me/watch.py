"""Automatic meeting detection, Notion-style.

Notion's desktop app notices when your microphone becomes active and offers to
take notes. We use the same device-level signal: CoreAudio's
kAudioDevicePropertyDeviceIsRunningSomewhere on the default input device tells
us when *any* app (Zoom, Teams, Meet in a browser, FaceTime…) opens the mic.
Zoom additionally gets precise start/end detection via its in-meeting helper
process (CptHost), which only runs during an active call.
"""

from __future__ import annotations

import ctypes
import struct
import subprocess
from ctypes import byref, c_uint32, sizeof

_coreaudio = ctypes.CDLL(
    "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
)

_SYSTEM_OBJECT = 1  # kAudioObjectSystemObject


def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode())[0]


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("selector", c_uint32),
        ("scope", c_uint32),
        ("element", c_uint32),
    ]


def _get_u32_property(object_id: int, selector: str) -> int | None:
    addr = _PropertyAddress(_fourcc(selector), _fourcc("glob"), 0)
    value = c_uint32(0)
    size = c_uint32(sizeof(value))
    status = _coreaudio.AudioObjectGetPropertyData(
        c_uint32(object_id), byref(addr), 0, None, byref(size), byref(value)
    )
    return value.value if status == 0 else None


def mic_in_use() -> bool:
    """True if any process currently has the default input device running."""
    device = _get_u32_property(_SYSTEM_OBJECT, "dIn ")  # default input device
    if not device:
        return False
    running = _get_u32_property(device, "gone")  # ...DeviceIsRunningSomewhere
    return bool(running)


def zoom_meeting_active() -> bool:
    """Zoom runs its CptHost helper only while you're in a meeting."""
    return (
        subprocess.run(["pgrep", "-x", "CptHost"], capture_output=True).returncode == 0
    )


def detect_meeting() -> str | None:
    """Return a trigger label ('zoom' / 'mic') if a meeting seems active."""
    if zoom_meeting_active():
        return "zoom"
    if mic_in_use():
        return "mic"
    return None


def notify(title: str, message: str) -> None:
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{message}" with title "{title}"',
        ],
        capture_output=True,
    )
