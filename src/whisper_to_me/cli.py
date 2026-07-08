"""wtm — whisper-to-me: local, private meeting notes.

    wtm devices                 list audio input devices
    wtm record                  record a meeting, live-transcribe, summarize on stop
    wtm watch                   auto-detect meetings (mic activity) and take notes
    wtm transcribe FILE         transcribe an audio file into a note
    wtm summarize FILE          (re)summarize an existing transcript / text file
    wtm ask "QUESTION"          answer a question across all your notes (local RAG)
    wtm draft NOTE              draft a follow-up email from a note (local, to stdout)
    wtm templates               list meeting templates and where to add your own
    wtm simulate --mic F [--system F]   replay files through the live pipeline (testing)
    wtm export [--obsidian PATH]  copy notes into an Obsidian vault (local files only)
    wtm push NOTE               push one note to Notion (opt-in, confirmed — the
                                only path where anything leaves the machine)
    wtm serve                   run the local HTTP/WS daemon for a UI (127.0.0.1 only)
    wtm ui                      like `serve`, but also opens the UI in your browser
"""

from __future__ import annotations

import argparse
import signal
import sys
from datetime import datetime
from pathlib import Path

from rich.table import Table

from . import audio, notes
from . import summarize as summ
from .config import load_config
from .session import (
    console,
    load_transcriber,
    record_session,
    simulate_session,
    summarize_and_save,
)


def _default_notes_dir() -> str:
    """--notes-dir default: config.toml's notes_dir (e.g. an Obsidian vault
    folder) when set, else ~/MeetingNotes."""
    configured = load_config().notes_dir
    return str(configured or notes.DEFAULT_NOTES_DIR)


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
        template=getattr(args, "template", None),
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
        diarize=args.diarize,
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
        diarize=args.diarize,
    )
    _finish(args, transcript_lines, started, title)


def cmd_watch(args) -> None:
    from .runner import WatchOptions, watch_loop

    transcriber = load_transcriber(args.model, args.language)
    console.print(
        "[bold cyan]👂 Watching for meetings[/bold cyan] — recording starts "
        "automatically when your mic goes active (Zoom, Teams, Meet, …). "
        "Ctrl-C to quit.\n"
    )
    opts = WatchOptions(
        title=args.title,
        device=args.device,
        system_device=args.system_device,
        keep_echoes=args.keep_echoes,
        use_aec=not args.no_aec,
        poll=args.poll,
        silence_timeout=args.silence_timeout,
        notes_dir=Path(args.notes_dir),
        ollama_model=args.ollama_model,
        context=args.context,
        no_summary=args.no_summary,
        template=args.template,
        diarize=args.diarize,
    )
    watch_loop(transcriber, opts)


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
    user_notes = Path(args.user_notes).read_text(encoding="utf-8") if args.user_notes else ""
    if not summ.check_model(args.ollama_model):
        console.print(f"[red]Ollama model '{args.ollama_model}' unavailable.[/red]")
        sys.exit(1)
    with console.status(f"Summarizing with {args.ollama_model} (local)…"):
        summary, title, _facts = summ.summarize_meeting(
            text,
            model=args.ollama_model,
            context=args.context,
            user_notes=user_notes,
            template=args.template,
        )
    if title:
        console.print(f"[bold]{title}[/bold]\n")
    console.print(summary)


def cmd_ask(args) -> None:
    from . import chat

    if not summ.check_model(args.ollama_model):
        console.print(f"[red]Ollama model '{args.ollama_model}' unavailable.[/red]")
        sys.exit(1)
    with console.status(f"Searching your notes and asking {args.ollama_model} (local)…"):
        try:
            result = chat.answer_question(
                Path(args.notes_dir), args.question, model=args.ollama_model
            )
        except summ.OllamaError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)
    console.print(result["answer"])
    if result["sources"]:
        console.print("\n[dim]Sources:[/dim]")
        for s in result["sources"]:
            console.print(f"  [dim]\\[{s['n']}] {s['title']} ({s['name']})[/dim]")


def cmd_draft(args) -> None:
    from . import followup

    path = Path(args.notes_dir) / args.note
    if not path.is_file():
        console.print(f"[red]No such note: {path}[/red]")
        sys.exit(1)
    if not summ.check_model(args.ollama_model):
        console.print(f"[red]Ollama model '{args.ollama_model}' unavailable.[/red]")
        sys.exit(1)
    with console.status(f"Drafting a follow-up with {args.ollama_model} (local)…"):
        try:
            draft = followup.draft_followup(
                path.read_text(encoding="utf-8"), model=args.ollama_model
            )
        except summ.OllamaError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)
    print(draft)  # plain stdout, pipeable to pbcopy — nothing is sent anywhere


def cmd_templates(_args) -> None:
    from . import templates as tmpl

    table = Table(title="Meeting templates")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Source")
    for t in tmpl.list_templates():
        table.add_row(t.name, t.description, "built-in" if t.builtin else "user")
    console.print(table)
    console.print(
        f"\n[dim]Add or override templates by dropping <name>.md files in "
        f"{tmpl.USER_TEMPLATES_DIR}. A meeting is auto-matched by title in "
        "`watch` mode; pass --template NAME to force one.[/dim]"
    )


def cmd_export(args) -> None:
    from .export import copy_to_vault, export_obsidian

    notes_dir = Path(args.notes_dir)
    vault = Path(args.obsidian).expanduser() if args.obsidian else load_config().obsidian_vault
    if vault is None:
        console.print(
            "[red]No vault path: pass --obsidian PATH, connect a vault in the UI "
            "(Settings → Connections), or set \\[obsidian] vault in "
            "~/.config/whisper-to-me/config.toml.[/red]"
        )
        sys.exit(2)

    if args.note:
        src = notes_dir / args.note
        if not src.is_file():
            console.print(f"[red]No such note: {src}[/red]")
            sys.exit(1)
        dest = copy_to_vault(src, vault, overwrite=args.overwrite)
        if dest is None:
            console.print(f"[yellow]Already in the vault (use --overwrite): {vault / src.name}[/yellow]")
        else:
            console.print(f"[bold green]Copied to vault:[/bold green] {dest}")
        return

    copied, skipped = export_obsidian(notes_dir, vault)
    console.print(
        f"[bold green]Exported {len(copied)} note(s)[/bold green] to {vault}"
        + (f" — skipped {len(skipped)} already there" if skipped else "")
    )


def cmd_push(args) -> None:
    """The one deliberate exception to "nothing leaves the machine": pushes a
    single note to Notion, only after showing exactly what will be sent and
    getting a yes. Never call this from anything automatic."""
    from . import notion_export

    cfg = load_config()
    if not cfg.notion_configured:
        console.print(
            "[red]Notion is not configured — connect it in the UI (Settings → "
            "Connections) or set \\[notion] token and database_id in "
            "~/.config/whisper-to-me/config.toml.[/red]"
        )
        sys.exit(2)
    path = Path(args.notes_dir) / args.note
    if not path.is_file():
        console.print(f"[red]No such note: {path}[/red]")
        sys.exit(1)

    preview = notion_export.preview_push(path, cfg.notion_database_id)
    console.rule("[bold]This note will be sent to api.notion.com[/bold]")
    console.print(path.read_text(encoding="utf-8"))
    console.rule()
    console.print(
        f"Title: [bold]{preview.title}[/bold] — {preview.block_count} blocks, "
        f"{preview.char_count} chars → Notion database {preview.database_id}"
    )
    if not args.yes:
        answer = input("Push this note to Notion? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            console.print("[yellow]Aborted — nothing was sent.[/yellow]")
            return
    try:
        url = notion_export.push_note(path, cfg.notion_token, cfg.notion_database_id)
    except notion_export.NotionError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    console.print(f"[bold green]Pushed to Notion:[/bold green] {url}")


def cmd_serve(args) -> None:
    from .server import ServerOptions, run_server

    opts = ServerOptions(
        model=args.model,
        language=args.language,
        ollama_model=args.ollama_model,
        notes_dir=Path(args.notes_dir),
        context=args.context,
        device=args.device,
        system_device=args.system_device,
        keep_echoes=args.keep_echoes,
        use_aec=not args.no_aec,
        poll=args.poll,
        silence_timeout=args.silence_timeout,
    )
    console.print(
        f"[bold cyan]wtm serve[/bold cyan] — http://127.0.0.1:{args.port} "
        "(loopback only; Ctrl-C to stop)\n"
    )
    run_server(opts, port=args.port, open_browser=args.open_browser)


def _add_serve_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", type=int, default=8737, help="TCP port to listen on (127.0.0.1 only)")
    p.add_argument("--device", type=int, default=None, help="input device index (see `wtm devices`)")
    p.add_argument("--system-device", default="auto",
                   help="system-audio (call) input: 'auto' (default), device index, or 'off'")
    p.add_argument("--keep-echoes", action="store_true",
                   help="disable the filter that drops mic lines duplicating system audio")
    p.add_argument("--no-aec", action="store_true",
                   help="disable acoustic echo cancellation of system audio from the mic")
    p.add_argument("--poll", type=float, default=3.0, help="seconds between meeting checks (watch sessions)")
    p.add_argument(
        "--silence-timeout", type=float, default=120.0,
        help="stop watch recordings after this many seconds of silence",
    )
    p.add_argument("--model", default="large-v3-turbo",
                   help="Whisper model: large-v3-turbo (default, most accurate) / medium / small / tiny")
    p.add_argument("--language", default=None, help="force language code, e.g. en, fr, ar")
    p.add_argument("--ollama-model", default=summ.DEFAULT_MODEL, help="local Ollama model for summaries")
    p.add_argument("--context", default="", help="hints for the summarizer (attendees, agenda…)")
    p.add_argument("--notes-dir", default=_default_notes_dir(), help="where notes are saved")


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
    p.add_argument("--notes-dir", default=_default_notes_dir(), help="where notes are saved")
    p.add_argument("--no-summary", action="store_true", help="save transcript only")
    p.add_argument("--template", default=None,
                   help="meeting template shaping the summary sections (see `wtm templates`)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="wtm", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="list audio input devices").set_defaults(func=cmd_devices)

    sub.add_parser(
        "templates", help="list meeting templates and where to add your own"
    ).set_defaults(func=cmd_templates)

    p_rec = sub.add_parser("record", help="record, live-transcribe and summarize a meeting")
    p_rec.add_argument("--device", type=int, default=None, help="input device index (see `wtm devices`)")
    p_rec.add_argument("--keep-echoes", action="store_true",
                       help="disable the filter that drops mic lines duplicating system audio")
    p_rec.add_argument("--no-aec", action="store_true",
                   help="disable acoustic echo cancellation of system audio from the mic")
    p_rec.add_argument("--diarize", action="store_true",
                       help="split 'Others' into Speaker A/B/C — beta, needs `uv sync --extra diarize`")
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
    p_w.add_argument("--diarize", action="store_true",
                     help="split 'Others' into Speaker A/B/C — beta, needs `uv sync --extra diarize`")
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
    p_sim.add_argument("--diarize", action="store_true",
                       help="split 'Others' into Speaker A/B/C — beta, needs `uv sync --extra diarize`")
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
    p_su.add_argument("--user-notes", metavar="FILE", default=None,
                      help="your own notes (a file) to expand from — guides the summary")
    p_su.add_argument("--template", default=None,
                      help="meeting template shaping the summary sections (see `wtm templates`)")
    p_su.set_defaults(func=cmd_summarize)

    p_ask = sub.add_parser("ask", help="ask a question across all your notes (local RAG)")
    p_ask.add_argument("question")
    p_ask.add_argument("--ollama-model", default=summ.DEFAULT_MODEL)
    p_ask.add_argument("--notes-dir", default=_default_notes_dir(), help="where notes are read from")
    p_ask.set_defaults(func=cmd_ask)

    p_draft = sub.add_parser(
        "draft", help="draft a follow-up email from a note (local; prints to stdout)"
    )
    p_draft.add_argument("note", help="note filename (see the notes directory)")
    p_draft.add_argument("--ollama-model", default=summ.DEFAULT_MODEL)
    p_draft.add_argument("--notes-dir", default=_default_notes_dir(), help="where notes are read from")
    p_draft.set_defaults(func=cmd_draft)

    p_ex = sub.add_parser("export", help="copy notes into an Obsidian vault (local files only)")
    p_ex.add_argument("--obsidian", metavar="PATH", nargs="?", const="", default=None,
                      help="vault folder (default: [obsidian] vault from config.toml)")
    p_ex.add_argument("--note", default=None, help="export a single note by filename")
    p_ex.add_argument("--overwrite", action="store_true",
                      help="replace a vault copy that already exists (single-note only)")
    p_ex.add_argument("--notes-dir", default=_default_notes_dir(), help="where notes are read from")
    p_ex.set_defaults(func=cmd_export)

    p_push = sub.add_parser(
        "push",
        help="push ONE note to Notion — opt-in, confirmed, the only network export",
    )
    p_push.add_argument("note", help="note filename (see the notes directory)")
    p_push.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_push.add_argument("--notes-dir", default=_default_notes_dir(), help="where notes are read from")
    p_push.set_defaults(func=cmd_push)

    p_serve = sub.add_parser("serve", help="run the local HTTP/WS daemon for a UI (127.0.0.1 only)")
    _add_serve_args(p_serve)
    p_serve.set_defaults(func=cmd_serve, open_browser=False)

    p_ui = sub.add_parser("ui", help="like `serve`, but also opens the UI in your browser")
    _add_serve_args(p_ui)
    p_ui.set_defaults(func=cmd_serve, open_browser=True)

    args = parser.parse_args()

    template = getattr(args, "template", None)
    if template is not None:
        from . import templates as tmpl

        if tmpl.load_template(template) is None:
            console.print(f"[red]Unknown template '{template}'. See `wtm templates`.[/red]")
            sys.exit(2)

    # A polite kill (SIGTERM) should behave like Ctrl-C: stop recording,
    # summarize, and save — never drop the transcript.
    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)

    args.func(args)


if __name__ == "__main__":
    main()
