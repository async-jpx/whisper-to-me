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

### Start: `watch.detect_meeting()`

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
   process; `Recorder.helper_pid` reports it). ScreenCaptureKit's `replayd`
   daemon is skipped too: it runs audio input whenever system audio is being
   captured, our own tap included, so counting it kept this signal from ever
   firing. When no *other* process has
   run input for `MIC_RELEASE_GRACE` (10 s — device switches and reconnect
   blips re-grab quickly), the meeting is over.
   Two guards make this safe:
   - it only arms after another process *was* seen on the mic during this
     recording (a raced detection can't insta-stop the session);
   - on older macOS or any CoreAudio error the function returns `None` and
     the signal is simply absent — never a false stop.
3. **Silence timeout** (pre-existing fallback): no audio above the energy
   gate on any source for `--silence-timeout` seconds (default 120).

### The lifecycle: always-on detection and a timed prompt

There is no watch mode. Detection runs from the moment `wtm serve` starts until
it shuts down, and the daemon's state is one value (`runner.State`):

```
          ┌──────────── meeting ends unanswered ─────────────┐
          ▼                                                   │
boot ──▶ idle ──detect──▶ prompting ──Record──▶ recording ──end/Stop──▶ summarizing ──▶ idle (sits out)
          ▲                │   │
          │                │   └──Dismiss, or 60 s unanswered──▶ idle (sits out)
          │                └──Start recording / simulate──▶ recording (manual / simulate)
          └── sitting out clears once the meeting's trigger goes away
```

- **Idle.** The detector polls `detect_meeting()` every `--poll` seconds
  (default 3). There is no "watching" state on the wire: idle *is* watching.
- **Prompting.** A detected meeting opens a prompt with a random id and a
  60-second deadline. The calendar/Zoom title hint is fetched before the
  prompt opens, so the user gets the full 60 seconds. The Whisper model
  preloads in the background so an accepted meeting starts transcribing at
  once. The status frame carries `prompt.expires_in_s`, computed when the
  frame is sent; clients only display the countdown.
- **Answers name the prompt.** `POST /api/prompts/{id}` with
  `{"answer": "record" | "dismiss"}`. Any answer that is not for the live
  prompt, or that arrives after its deadline, gets 409 `prompt expired`. A
  late click can never start the next meeting's recording.
- **The timeout is a dismiss.** At the deadline the daemon resolves the prompt
  on its own clock (its sleep is clamped to the deadline) and broadcasts plain
  `idle` at once, so every prompt surface hides immediately.
- **Sitting out.** After a dismiss, a timeout, or the end of *any* session,
  the daemon is `Idle(sitting_out=True)`: it does not prompt again for the
  meeting that is still live. It clears when `detect_meeting()` returns None,
  so the next meeting prompts normally. If the meeting ends while its prompt
  is up, the prompt closes and nothing is sat out.
- **"Don't ask for this app".** When the prompt knows which app holds the
  mic, it offers a checkbox; answering with it ticked (`ignore_app: true`)
  adds the app to `[detection] ignored_apps` in config.toml. From then on a
  mic that only ignored apps hold never prompts (Zoom's CptHost trigger is
  skipped when "Zoom" is ignored). A process with no app bundle can't be
  named, so it still prompts. Settings → General lists and removes them.
- **Manual record and simulate supersede a prompt.** "Start recording" is
  allowed while prompting; the prompt goes away in the same transition.
- **Stop works on every recording.** `POST /api/record/stop` stops manual and
  accepted (detected) recordings alike and never stops detection. A detected
  recording also ends on its own through the three end signals above.
- **While a session runs, detection pauses.** Our own recorder keeps the
  input device running, so the `mic` trigger would always be true.
- **Shutdown.** SIGTERM (the desktop app's quit) stops the active recording
  and waits for the note to be saved and summarized before the daemon exits.

The prompt surfaces are the desktop app's always-on-top overlay window
(`/static/prompt.html`, never steals focus) and its tray menu. `wtm serve`
without the desktop app has no prompt surface, so its prompts time out.

## Verification status

The state machine (timeout, sit-out, stale answers, supersede), the auto-stop
logic (grace, re-grab reset, arming guard, zoom end, helper-pid exclusion) and
the API (prompt answers, detected stop, shutdown save) are covered by
`tests/test_meeting_state.py` + `tests/test_api.py`, which run mic-free on any
OS against a scripted detection probe. The CoreAudio process-object selectors are taken from the
macOS 14 SDK (`'prs#'`/`'ppid'`/`'piri'`, verified against Apple's generated
bindings); the live behavior of `mic_in_use_by_others` — including whether
ScreenCaptureKit's capture shows up as input for some process we don't spawn —
still needs a real-Mac, real-meeting pass. If it misbehaves there, the
built-in degradation (return `None` → silence timeout only) is the designed
fallback.
