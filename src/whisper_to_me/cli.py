"""wtm — whisper-to-me: local, private meeting notes.

    wtm devices                 list audio input devices
    wtm record                  record a meeting, live-transcribe, summarize on stop
    wtm watch                   auto-detect meetings (mic activity) and take notes
    wtm transcribe FILE         transcribe an audio file into a note
    wtm summarize FILE          (re)summarize an existing transcript / text file
    wtm simulate --mic F [--system F]   replay files through the live pipeline (testing)
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.table import Table

from . import audio, notes
from . import summarize as summ
from .session import (
    console,
    load_transcriber,
    record_session,
    simulate_session,
    summarize_and_save,
)


def cmd_devices(_args) -> None:
    table = Table(title="Audio input devices")
    table.add_column("Index", justify="right")
    table.add_column("Name")
    table.add_column("Channels", justify="right")
    table.add_column("")
    for dev in audio.list_devices():
        table.add_row(
            str(dev["index"]),
            dev["name"],
            str(dev["channels"]),
            "← default" if dev["default"] else "",
        )
    console.print(table)
    console.print(
        "\n[dim]System audio (remote participants) is captured automatically "
        "via the native tap; use --device only to pick a different mic.[/dim]"
    )


def _finish(
    args, transcript_lines, started, title: str, auto_title: bool | None = None
) -> None:
    if auto_title is None:
        # No --title given: let the summarizer name the meeting.
        auto_title = args.title is None
    summarize_and_save(
        title,
        transcript_lines,
        started,
        Path(args.notes_dir),
        ollama_model=args.ollama_model,
        context=args.context,
        no_summary=args.no_summary,
        auto_title=auto_title,
    )


def cmd_record(args) -> None:
    title = args.title or f"Meeting {datetime.now():%d %b %H:%M}"
    transcriber = load_transcriber(args.model, args.language)
    console.print(
        f"[bold green]● Recording[/bold green] '{title}' — speak away. "
        "Press [bold]Ctrl-C[/bold] to stop and summarize.\n"
    )
    transcript_lines, started = record_session(
        transcriber,
        title,
        Path(args.notes_dir),
        device=args.device,
        system_device=args.system_device,
        keep_echoes=args.keep_echoes,
        use_aec=not args.no_aec,
    )
    _finish(args, transcript_lines, started, title)


def cmd_simulate(args) -> None:
    title = args.title or f"Simulation {datetime.now():%d %b %H:%M}"
    transcriber = load_transcriber(args.model, args.language)
    console.print(
        f"[bold green]▶ Simulating[/bold green] mic='{args.mic}'"
        + (f" system='{args.system}'" if args.system else "")
        + " — replaying through the live pipeline.\n"
    )
    transcript_lines, started = simulate_session(
        transcriber,
        title,
        Path(args.notes_dir),
        args.mic,
        system_path=args.system,
        keep_echoes=args.keep_echoes,
        use_aec=not args.no_aec,
    )
    _finish(args, transcript_lines, started, title)


def cmd_watch(args) -> None:
    from . import watch

    transcriber = load_transcriber(args.model, args.language)
    console.print(
        "[bold cyan]👂 Watching for meetings[/bold cyan] — recording starts "
        "automatically when your mic goes active (Zoom, Teams, Meet, …). "
        "Ctrl-C to quit.\n"
    )
    try:
        while True:
            trigger = watch.detect_meeting()
            if trigger is None:
                time.sleep(args.poll)
                continue

            # Title priority: --title > calendar event / Zoom window topic >
            # placeholder that the summarizer replaces with an inferred title.
            hint = watch.meeting_title_hint(trigger) if args.title is None else None
            title = args.title or hint or (
                f"{'Zoom meeting' if trigger == 'zoom' else 'Meeting'} "
                f"{datetime.now():%d %b %H:%M}"
            )
            watch.notify("whisper-to-me", f"Meeting detected — taking notes: {title}")
            console.print(
                f"[bold green]● Meeting detected ({trigger})[/bold green] — recording '{title}'\n"
            )

            last_speech = time.monotonic()

            def should_stop(recorders) -> bool:
                nonlocal last_speech
                if max(r.peak_level for r in recorders) >= audio.SILENCE_RMS:
                    last_speech = time.monotonic()
                if trigger == "zoom" and not watch.zoom_meeting_active():
                    console.print("[yellow]Zoom meeting ended.[/yellow]")
                    return True
                if time.monotonic() - last_speech > args.silence_timeout:
                    console.print(
                        f"[yellow]No audio for {args.silence_timeout:.0f}s — meeting seems over.[/yellow]"
                    )
                    return True
                return False

            transcript_lines, started = record_session(
                transcriber,
                title,
                Path(args.notes_dir),
                device=args.device,
                system_device=args.system_device,
                should_stop=should_stop,
                keep_echoes=args.keep_echoes,
                use_aec=not args.no_aec,
            )
            # A real name from the calendar/Zoom wins; only infer when we
            # fell back to the timestamp placeholder.
            _finish(
                args,
                transcript_lines,
                started,
                title,
                auto_title=args.title is None and hint is None,
            )
            watch.notify("whisper-to-me", f"Notes saved for: {title}")

            # Wait until the trigger clears so we don't instantly re-record
            # the tail of the same meeting.
            while watch.detect_meeting():
                time.sleep(args.poll)
            console.print("\n[bold cyan]👂 Watching for meetings…[/bold cyan]\n")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/dim]")


def cmd_transcribe(args) -> None:
    title = args.title or Path(args.file).stem
    transcriber = load_transcriber(args.model, args.language)
    with console.status(f"Transcribing {args.file}…"):
        segments = transcriber.transcribe_file(args.file)
    lines = []
    for start, text in segments:
        stamp = f"{int(start // 3600)}:{int(start % 3600 // 60):02d}:{int(start % 60):02d}"
        lines.append((stamp, text))
        console.print(f"[dim][{stamp}][/dim] {text}")
    _finish(args, lines, datetime.now(), title)


def cmd_summarize(args) -> None:
    text = Path(args.file).read_text(encoding="utf-8")
    if not summ.check_model(args.ollama_model):
        console.print(f"[red]Ollama model '{args.ollama_model}' unavailable.[/red]")
        sys.exit(1)
    with console.status(f"Summarizing with {args.ollama_model} (local)…"):
        summary, title = summ.summarize_meeting(
            text, model=args.ollama_model, context=args.context
        )
    if title:
        console.print(f"[bold]{title}[/bold]\n")
    console.print(summary)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--title", default=None,
                   help="meeting title for the note (default: inferred from the conversation)")
    p.add_argument("--model", default="large-v3-turbo",
                   help="Whisper model: large-v3-turbo (default, most accurate) / medium / small / tiny")
    p.add_argument("--system-device", default="auto",
                   help="system-audio (call) input: 'auto' (default), device index, or 'off'")
    p.add_argument("--language", default=None, help="force language code, e.g. en, fr, ar")
    p.add_argument("--ollama-model", default=summ.DEFAULT_MODEL, help="local Ollama model for summaries")
    p.add_argument("--context", default="", help="hints for the summarizer (attendees, agenda…)")
    p.add_argument("--notes-dir", default=str(notes.DEFAULT_NOTES_DIR), help="where notes are saved")
    p.add_argument("--no-summary", action="store_true", help="save transcript only")


def main() -> None:
    parser = argparse.ArgumentParser(prog="wtm", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="list audio input devices").set_defaults(func=cmd_devices)

    p_rec = sub.add_parser("record", help="record, live-transcribe and summarize a meeting")
    p_rec.add_argument("--device", type=int, default=None, help="input device index (see `wtm devices`)")
    p_rec.add_argument("--keep-echoes", action="store_true",
                       help="disable the filter that drops mic lines duplicating system audio")
    p_rec.add_argument("--no-aec", action="store_true",
                   help="disable acoustic echo cancellation of system audio from the mic")
    _add_common(p_rec)
    p_rec.set_defaults(func=cmd_record)

    p_w = sub.add_parser("watch", help="auto-detect meetings and take notes, Notion-style")
    p_w.add_argument("--device", type=int, default=None, help="input device index (see `wtm devices`)")
    p_w.add_argument("--poll", type=float, default=3.0, help="seconds between meeting checks")
    p_w.add_argument(
        "--silence-timeout", type=float, default=120.0,
        help="stop recording after this many seconds of silence",
    )
    p_w.add_argument("--keep-echoes", action="store_true",
                     help="disable the filter that drops mic lines duplicating system audio")
    p_w.add_argument("--no-aec", action="store_true",
                   help="disable acoustic echo cancellation of system audio from the mic")
    _add_common(p_w)
    p_w.set_defaults(func=cmd_watch)

    p_sim = sub.add_parser(
        "simulate",
        help="replay audio files through the live pipeline (no devices) — for testing",
    )
    p_sim.add_argument("--mic", required=True, help="audio file replayed as the microphone (You)")
    p_sim.add_argument("--system", default=None, help="audio file replayed as system audio (Others)")
    p_sim.add_argument("--keep-echoes", action="store_true",
                       help="disable the filter that drops mic lines duplicating system audio")
    p_sim.add_argument("--no-aec", action="store_true",
                   help="disable acoustic echo cancellation of system audio from the mic")
    _add_common(p_sim)
    p_sim.set_defaults(func=cmd_simulate)

    p_tr = sub.add_parser("transcribe", help="transcribe an audio file into a note")
    p_tr.add_argument("file")
    _add_common(p_tr)
    p_tr.set_defaults(func=cmd_transcribe)

    p_su = sub.add_parser("summarize", help="summarize an existing transcript file")
    p_su.add_argument("file")
    p_su.add_argument("--ollama-model", default=summ.DEFAULT_MODEL)
    p_su.add_argument("--context", default="")
    p_su.set_defaults(func=cmd_summarize)

    args = parser.parse_args()

    # A polite kill (SIGTERM) should behave like Ctrl-C: stop recording,
    # summarize, and save — never drop the transcript.
    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)

    args.func(args)


if __name__ == "__main__":
    main()
