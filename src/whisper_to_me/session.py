"""Recording-session orchestration: capture sources, live transcription
workers, and the summarize-and-save step shared by `record` and `watch`."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from rich.console import Console

from . import audio, dedup, notes
from . import summarize as summ

console = Console()

StopCondition = Callable[[list[audio.Recorder]], bool]

# An event sink is just a callable taking one JSON-able dict — status changes,
# transcript lines, echo-filter counts, summarizing/saved/error notices. Both
# `record_session` and `summarize_and_save` emit through one, so a UI daemon
# (server.py) can subscribe by passing its own sink; the CLI keeps working
# unchanged because the default sink reproduces today's console output.
EventSink = Callable[[dict], None]


class ConsoleSink:
    """Default sink: prints exactly what this module printed before events
    existed. Stateful only to keep the "Summarizing…" spinner going between
    the `summarizing` event and the `saved`/`error` that ends it."""

    def __init__(self) -> None:
        self._status = None

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def __call__(self, event: dict) -> None:
        etype = event["type"]
        if etype == "status":
            return  # cmd_record/cmd_watch already announce their own start
        if etype == "line":
            console.print(
                f"[dim][{event['stamp']}][/dim] [bold]{event['speaker']}:[/bold] {event['text']}"
            )
        elif etype == "echoes_dropped":
            console.print(
                f"[dim]Echo filter: removed {event['count']} mic line(s) that were "
                "the speakers bleeding into the microphone.[/dim]"
            )
        elif etype == "brief":
            console.print(f"[dim]Last time — {event['title']}: {event['tldr']}[/dim]")
        elif etype == "summarizing":
            self._stop_status()
            self._status = console.status(f"Summarizing with {event['model']} (local)…")
            self._status.start()
        elif etype == "saved":
            self._stop_status()
            console.print(f"\n[bold green]Note saved:[/bold green] {event['path']}")
            if event.get("summary"):
                console.rule("Summary")
                console.print(event["summary"])
        elif etype == "error":
            self._stop_status()
            console.print(f"[red]{event['message']}[/red]")


_console_sink = ConsoleSink()


def resolve_sink(events: EventSink | None) -> EventSink:
    return events if events is not None else _console_sink


def load_transcriber(model: str, language: str | None):
    from .transcribe import Transcriber  # deferred: heavy import

    with console.status(f"Loading Whisper model '{model}' (downloads once, then offline)…"):
        return Transcriber(model_size=model, language=language)


def _system_audio_sources(system_device: str) -> list[tuple[str, audio.Recorder]]:
    """Build 'Others' sources: native system tap, else loopback devices."""
    if system_device == "off":
        return []

    if system_device != "auto":  # explicit device index forces that device
        idx = int(system_device)
        dev = next((d for d in audio.list_devices() if d["index"] == idx), None)
        if dev is None:
            console.print(f"[red]No input device with index {idx}.[/red]")
            return []
        console.print(f"[dim]Capturing call audio from '{dev['name']}'.[/dim]")
        return [("Others", audio.Recorder(device=idx))]

    tap_binary = audio.build_system_tap()
    if tap_binary is not None:
        console.print(
            "[dim]Capturing system audio natively — remote participants in any "
            "app (Zoom, Teams, Meet…) are transcribed. macOS may ask for "
            "System Audio Recording permission once.[/dim]"
        )
        return [("Others", audio.SystemAudioTap(tap_binary))]

    loopbacks = audio.find_loopback_devices()
    for dev in loopbacks:
        console.print(f"[dim]Capturing call audio from '{dev['name']}' (fallback).[/dim]")
    if not loopbacks:
        console.print(
            "[dim]No system-audio capture available — only the mic will be "
            "heard. Install Xcode Command Line Tools (for the native tap) or "
            "pass --system-device.[/dim]"
        )
    return [("Others", audio.Recorder(device=d["index"])) for d in loopbacks]


def record_session(
    transcriber,
    title: str,
    notes_dir: Path,
    device: int | None = None,
    system_device: str = "auto",
    should_stop: StopCondition | None = None,
    sources: list[tuple[str, audio.Recorder]] | None = None,
    started: datetime | None = None,
    keep_echoes: bool = False,
    use_aec: bool = True,
    diarize: bool = False,
    events: EventSink | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[list[tuple[str, str]], datetime]:
    """Record (mic + system audio) until Ctrl-C, should_stop(recorders), or
    stop_event.is_set() — whichever comes first.

    Returns (transcript_lines, started); lines are (stamp, text) sorted by
    capture time, with speaker labels when there is more than one source.
    """
    sink = resolve_sink(events)
    if sources is None:
        sources = [("You", audio.Recorder(device=device))]
        sources += _system_audio_sources(system_device)

    if use_aec and len(sources) == 2:
        # Cancel speaker bleed from the mic signal itself, using the system
        # source as the reference. The text-level echo filter stays on as a
        # backstop for the convergence window and residuals.
        from .echo_cancel import EchoCanceller  # deferred: numpy-heavy

        canceller = EchoCanceller(
            realtime=not isinstance(sources[1][1], audio.FileRecorder)
        )
        sources[1][1].block_listener = canceller.add_reference
        sources[0][1].preprocess = canceller.process

    # Speaker diarization within "Others" (beta, opt-in). Embed each Others
    # utterance now; cluster + relabel post-hoc, after echo removal and sort.
    embedder = None
    others_embeddings: dict[tuple[datetime, str], object] = {}
    emb_lock = threading.Lock()
    if diarize and len(sources) > 1:
        from . import diarize as diar

        if diar.available():
            embedder = diar.SpeakerEmbedder()
        else:
            console.print(
                "[yellow]Diarization requested but not installed — run "
                "`uv sync --extra diarize`. Recording without speaker labels.[/yellow]"
            )

    started = started or datetime.now()
    sink({"type": "status", "state": "recording", "title": title, "started": started.isoformat()})
    raw_lines: list[dedup.Line] = []  # (capture_time, duration_s, speaker, text)
    suppressed = 0  # echoes caught live, before they hit the journal
    label = len(sources) > 1
    filter_echoes = label and not keep_echoes
    # Live journal: every line lands on disk immediately, so a crash or kill
    # can never lose the transcript. summarize_and_save rewrites this file
    # with the time-sorted transcript and the summary at the end.
    live_path = notes.start_live_note(title, started, notes_dir)

    def make_worker(speaker: str, recorder: audio.Recorder) -> threading.Thread:
        def worker() -> None:
            nonlocal suppressed
            while True:
                item = recorder.chunks.get()
                if item is None:
                    return
                captured_at, chunk = item
                # Each Whisper segment (~a sentence) gets its own absolute
                # time, so sources interleave at sentence granularity even
                # when a chunk runs long.
                for start_s, end_s, text in transcriber.transcribe_chunk(chunk):
                    seg_at = captured_at + timedelta(seconds=start_s)
                    duration = end_s - start_s
                    # Live pass: skip a mic segment that is already on screen
                    # as an "Others" segment (speaker bleed). The final pass
                    # below also catches the reverse arrival order.
                    if (
                        filter_echoes
                        and speaker == dedup.ECHO_SPEAKER
                        and dedup.matches_any(seg_at, duration, text, raw_lines)
                    ):
                        suppressed += 1
                        continue
                    stamp = str(seg_at - started).split(".")[0]
                    line = f"**{speaker}:** {text}" if label else text
                    raw_lines.append((seg_at, duration, speaker, text))
                    notes.append_line(live_path, stamp, line)
                    # Diarization: embed this Others utterance (never the mic).
                    # Runs on the Others worker; embedding is far cheaper than
                    # the Whisper decode it follows. Failures return None.
                    if embedder is not None and speaker == dedup.CLEAN_SPEAKER:
                        seg_audio = chunk[int(start_s * 16_000) : int(end_s * 16_000)]
                        vec = embedder.embed(seg_audio)
                        if vec is not None:
                            with emb_lock:
                                others_embeddings[(seg_at, text)] = vec
                    # "label" is a CLI-only helper: ConsoleSink always prints
                    # the speaker (the pre-event CLI did too, even solo), but
                    # the wire contract wants speaker=null for single-source
                    # sessions — server.py nulls it and strips this field.
                    sink({
                        "type": "line",
                        "stamp": stamp,
                        "speaker": speaker,
                        "text": text,
                        "label": label,
                    })

        return threading.Thread(target=worker)

    workers = [make_worker(speaker, rec) for speaker, rec in sources]
    started_recs: list[audio.Recorder] = []
    try:
        for _, rec in sources:
            rec.start()
            started_recs.append(rec)
    except Exception:
        # A later source failing to start must not leave an earlier one (the
        # mic!) capturing forever — that leak breaks every following session
        # until the daemon restarts.
        for rec in started_recs:
            try:
                rec.stop()
            except Exception:
                pass
        raise
    for w in workers:
        w.start()

    recorders = [rec for _, rec in sources]
    try:
        while any(w.is_alive() for w in workers):
            workers[0].join(timeout=0.5)
            if should_stop is not None and should_stop(recorders):
                break
            if stop_event is not None and stop_event.is_set():
                break
    except KeyboardInterrupt:
        pass
    console.print("\n[yellow]Stopping… transcribing remaining audio.[/yellow]")
    stop_error: Exception | None = None
    for _, rec in sources:
        try:
            rec.stop()
        except Exception as exc:  # keep stopping the rest: a raised stop must
            stop_error = exc      # not leak the other source's device/helper
    for w in workers:
        w.join()
    if stop_error is not None:
        sink({
            "type": "error",
            "message": f"An audio source failed to shut down cleanly: {stop_error}",
        })

    if filter_echoes:
        kept = dedup.drop_echoes(raw_lines)
        dropped = suppressed + len(raw_lines) - len(kept)
        if dropped:
            sink({"type": "echoes_dropped", "count": dropped})
        raw_lines = kept

    raw_lines.sort(key=lambda line: line[0])

    # Relabel "Others" into per-speaker labels AFTER echo removal (so dropped
    # lines don't vote and the filter's "Others" comparisons still held) and
    # BEFORE _merge_turns (so turns follow real speakers). Empty result → the
    # audio didn't support ≥2 confident speakers; everything stays "Others".
    if embedder is not None and others_embeddings:
        from . import diarize as diar

        speaker_labels = diar.assign_labels(raw_lines, others_embeddings)
        if speaker_labels:
            raw_lines = [
                (t, dur, speaker_labels.get((t, text), sp) if sp == dedup.CLEAN_SPEAKER else sp, text)
                for t, dur, sp, text in raw_lines
            ]

    transcript_lines = [
        (str(t - started).split(".")[0], f"**{speaker}:** {text}" if label else text)
        for t, speaker, text in _merge_turns(raw_lines)
    ]
    return transcript_lines, started


# A new turn starts when the speaker changes or after this much silence —
# keeps timestamps meaningful inside long monologues.
TURN_GAP_SECONDS = 8.0


def _merge_turns(
    lines: list[dedup.Line],
) -> list[tuple[datetime, str, str]]:
    """Coalesce time-sorted segments into (start, speaker, text) turns."""
    turns: list[tuple[datetime, str, str]] = []
    turn_end: datetime | None = None
    for t, dur, speaker, text in lines:
        seg_end = t + timedelta(seconds=dur)
        if (
            turns
            and turns[-1][1] == speaker
            and (t - turn_end).total_seconds() <= TURN_GAP_SECONDS
        ):
            start, _, so_far = turns[-1]
            turns[-1] = (start, speaker, f"{so_far} {text}")
            turn_end = max(turn_end, seg_end)
        else:
            turns.append((t, speaker, text))
            turn_end = seg_end
    return turns


def simulate_session(
    transcriber,
    title: str,
    notes_dir: Path,
    mic_path: str,
    system_path: str | None = None,
    keep_echoes: bool = False,
    use_aec: bool = True,
    diarize: bool = False,
    events: EventSink | None = None,
) -> tuple[list[tuple[str, str]], datetime]:
    """Replay audio files through the live pipeline — chunking, transcription,
    echo filtering, merging — with no audio devices. The regression-test path:
    the mic file plays as "You", the system file as "Others", on one timeline.
    """
    epoch = datetime.now()
    sources: list[tuple[str, audio.Recorder]] = [
        ("You", audio.FileRecorder(mic_path, epoch))
    ]
    if system_path:
        sources.append(("Others", audio.FileRecorder(system_path, epoch)))
    return record_session(
        transcriber,
        title,
        notes_dir,
        sources=sources,
        started=epoch,
        keep_echoes=keep_echoes,
        events=events,
        use_aec=use_aec,
        diarize=diarize,
        should_stop=lambda recorders: all(r.finished for r in recorders),
    )


def summarize_and_save(
    title: str,
    transcript_lines: list[tuple[str, str]],
    started: datetime,
    notes_dir: Path,
    ollama_model: str = summ.DEFAULT_MODEL,
    context: str = "",
    no_summary: bool = False,
    auto_title: bool = False,
    user_notes: str = "",
    template: str | None = None,
    events: EventSink | None = None,
) -> Path:
    sink = resolve_sink(events)
    summary = None
    inferred_title = None
    attendees: list[str] = []
    if not no_summary and transcript_lines:
        text = "\n".join(t for _, t in transcript_lines)
        if not summ.check_model(ollama_model):
            sink({
                "type": "error",
                "message": f"Ollama model '{ollama_model}' unavailable — "
                "saving transcript without summary.",
            })
        else:
            sink({"type": "summarizing", "model": ollama_model})
            try:
                summary, inferred_title, facts = summ.summarize_meeting(
                    text,
                    model=ollama_model,
                    context=context,
                    user_notes=user_notes,
                    template=template,
                )
                attendees = [str(a) for a in facts.get("attendees") or []]
            except summ.OllamaError as exc:
                sink({"type": "error", "message": str(exc)})

    final_title = title
    if auto_title and inferred_title:
        final_title = inferred_title
        console.print(f"[dim]Inferred title:[/dim] [bold]{final_title}[/bold]")

    path = notes.save_note(
        final_title,
        transcript_lines,
        summary,
        notes_dir,
        started,
        attendees=attendees,
        user_notes=user_notes,
    )
    # The live journal was created under the placeholder title; now that the
    # final note is safely on disk, drop the stale copy. Never delete before
    # the new file exists — a crash in between must leave one complete copy.
    live_path = notes.note_path(title, started, notes_dir)
    if live_path != path and live_path.exists():
        live_path.unlink()
    # "summary" here is CLI-only sugar for ConsoleSink's rule+body print; a
    # wire sink (server.py) strips it before broadcasting — the "saved" event
    # contract is just path/title/name, and a UI fetches the note body via
    # GET /api/notes/{name} instead of duplicating it over the socket.
    sink({
        "type": "saved",
        "path": str(path),
        "title": final_title,
        "name": path.name,
        "summary": summary,
    })
    return path
