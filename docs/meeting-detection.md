# Meeting detection: start, prompt, and auto-stop

How whisper-to-me decides a meeting has started and — the harder problem —
that it has *ended*, and why the flow is prompt-first. Everything below is a
purely local signal (CoreAudio properties, process checks, Calendar.app);
nothing here touches the network.

## What the competition does

Researched July 2026 across the bot-free note takers:

| Product | Start detection | End detection |
|---|---|---|
| **Granola** | Calendar sync (notification ~1 min before events with 2+ attendees) + ad-hoc detection when the microphone becomes active | Three signals combined: the calendar event's scheduled end time; **the call app releasing the microphone** (needs admin rights on managed Macs); a transcript-length heuristic ("has anyone said anything lately?") |
| **Notion AI Meeting Notes** | The desktop app "observes if a user has a process running that is actively using their microphone (e.g. Zoom)" and shows a notification/prompt — it explicitly does *not* listen to any audio to decide this | User confirms start ("Start transcribing") and stop; the prompt-first flow is the consent step |
| **Fathom / Otter / tl;dv** (bot-based) | A bot joins the call from the calendar link | The bot *is* in the call — it knows when the meeting ends first-hand |

Takeaways we adopted:

1. **Prompt, don't auto-record** (Notion): mic activity is a good detector but
   a bad trigger — dictation, voice memos, a quick FaceTime with family should
   not silently become meeting notes. Detection pops a small accept/ignore
   widget; recording starts only on an explicit yes. (It's also the consent
   moment for recording other participants.)
2. **"The call app let go of the mic" is the end signal** (Granola): scheduled
   end times lie (meetings run over) and silence thresholds are slow. The app
   releasing the microphone is prompt and accurate.
3. **Keep a silence timeout as the universal fallback** (Granola's transcript
   heuristic, our `--silence-timeout`): it needs no OS support and catches
   every case the sharper signals miss.

## Our signals

### Start (unchanged): `watch.detect_meeting()`

- **Zoom**: the `CptHost` helper process only exists during an active call —
  precise start *and* end.
- **Everything else** (Teams, Meet-in-a-browser, FaceTime…): CoreAudio's
  `kAudioDevicePropertyDeviceIsRunningSomewhere` on the default input device —
  true when *any* app has the mic open. This is the same signal Notion
  describes.

### End: three signals, first one wins

1. **Zoom**: `CptHost` exits (pre-existing).
2. **Mic release** (new, macOS 14+): while we record, the device-level signal
   is useless — *our own* recorder keeps the input device "running somewhere".
   macOS 14 added per-process audio objects, so
   `watch.mic_in_use_by_others()` walks
   `kAudioHardwarePropertyProcessObjectList` (`'prs#'`) and checks
   `kAudioProcessPropertyIsRunningInput` (`'piri'`) per process, comparing
   `kAudioProcessPropertyPID` (`'ppid'`) against our own pid **and our
   spawned helpers** (the ScreenCaptureKit system-audio tap is a child
   process; `Recorder.helper_pid` reports it). When no *other* process has
   run input for `MIC_RELEASE_GRACE` (10 s — device switches and reconnect
   blips re-grab quickly), the meeting is over.
   Two guards make this safe:
   - it only arms after another process *was* seen on the mic during this
     recording (a raced detection can't insta-stop the session);
   - on older macOS or any CoreAudio error the function returns `None` and
     the signal is simply absent — never a false stop.
3. **Silence timeout** (pre-existing fallback): no audio above the energy
   gate on any source for `--silence-timeout` seconds (default 120).

### The prompt flow (daemon default)

```
boot ──▶ watching ──detect──▶ prompting ──accept──▶ recording ──end──▶ summarize ──▶ watching
                                  │  │
                                  │  └─ignore──────▶ (sit out this meeting) ──▶ watching
                                  └─meeting ends unanswered─▶ watching
```

- `wtm serve` starts watching on boot (`--no-watch` / `[watch] auto_start =
  false` to disable) and asks before recording (`--auto-record` / `[watch]
  confirm = false` for the old behavior). The Whisper model is only loaded
  when a recording actually starts — a prompt preloads it in the background
  so an accepted meeting starts transcribing immediately.
- The prompt surfaces in three places, all driven by the same
  `state: "prompting"` status event: the web UI's floating card, the desktop
  app's small always-on-top overlay window (`/static/prompt.html`, top-right,
  never steals focus), and the tray menu ("Record this meeting" / "Ignore
  this meeting"). Answers all land on `POST /api/watch/respond
  {"accept": bool}`.
- `wtm watch` in a terminal keeps the old auto-record behavior — running it
  is itself the explicit intent to record.
- A manual "New meeting" (or a simulation) while the daemon is watching or
  prompting *preempts* the idle watch instead of failing with "busy", and the
  watch re-arms itself once that session's note is saved — the daemon always
  returns to its resting state. A watch that is actively recording is never
  preempted.

## Verification status

The state machine, prompt flow, decision plumbing, auto-stop logic (grace,
re-grab reset, arming guard, zoom end, helper-pid exclusion) and the API are
covered by `tests/test_watch_prompt.py` + `tests/test_api.py`, which run
mic-free on any OS. The CoreAudio process-object selectors are taken from the
macOS 14 SDK (`'prs#'`/`'ppid'`/`'piri'`, verified against Apple's generated
bindings); the live behavior of `mic_in_use_by_others` — including whether
ScreenCaptureKit's capture shows up as input for some process we don't spawn —
still needs a real-Mac, real-meeting pass. If it misbehaves there, the
built-in degradation (return `None` → silence timeout only) is the designed
fallback.
