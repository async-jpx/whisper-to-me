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


_GENERIC_ZOOM_TITLES = {"", "zoom", "zoom meeting", "zoom workplace", "zoom.us"}


def zoom_window_title() -> str | None:
    """The Zoom meeting window title, when it carries the actual topic.

    Needs the Automation/Accessibility permission for the terminal (macOS
    prompts once); returns None on denial, timeout, or a generic title.
    """
    try:
        out = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get title of every window of process "zoom.us"',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    titles = sorted((t.strip() for t in out.stdout.split(",")), key=len, reverse=True)
    for title in titles:
        if title.lower() not in _GENERIC_ZOOM_TITLES and not title.lower().startswith(
            "zoom share"
        ):
            return title
    return None


_CALENDAR_SCRIPT = """\
set nowD to current date
tell application "Calendar"
    repeat with c in calendars
        set evs to (every event of c whose allday event is false and \
start date is less than or equal to nowD and end date is greater than or equal to nowD)
        if (count of evs) > 0 then return summary of item 1 of evs
    end repeat
end tell
return ""
"""


def current_calendar_event() -> str | None:
    """Title of the Calendar.app event covering 'now' — a purely local query.

    Needs the Calendar automation permission (macOS prompts once); returns
    None on denial, timeout, or no current event.
    """
    try:
        out = subprocess.run(
            ["osascript", "-e", _CALENDAR_SCRIPT],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def meeting_title_hint(trigger: str) -> str | None:
    """Best local guess at the meeting's real name: the calendar event
    covering now, else the Zoom window topic. Best-effort and permission
    gated — None means 'let the summarizer infer one instead'."""
    title = current_calendar_event()
    if title is None and trigger == "zoom":
        title = zoom_window_title()
    return title


def notify(title: str, message: str) -> None:
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{message}" with title "{title}"',
        ],
        capture_output=True,
    )
