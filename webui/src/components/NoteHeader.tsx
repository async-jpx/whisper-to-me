/* The open note's header (title, when, app, length) and its "⋯" menu. */

import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import type { NoteMeta } from "../api/types";
import { noteTitleFromMd } from "../lib/markdown";
import { formatDuration } from "../lib/time";
import { Icon } from "./Icons";

const DAY = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
});
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

export function NoteHeader({ name, meta }: { name: string | null; meta: NoteMeta | undefined }) {
  // Its own subscription: the summary article must not re-render on
  // currentNoteMd (see NoteContainer), but the title should follow edits.
  const currentNoteMd = useStore((s) => s.currentNoteMd);
  const title = noteTitleFromMd(currentNoteMd ?? "") ?? meta?.title ?? name ?? "";
  const date = meta?.date ? new Date(meta.date) : null;

  return (
    <header className="note-head">
      <h1 className="note-head-title">{title}</h1>
      <div className="note-head-facts">
        {date && !Number.isNaN(date.getTime()) && (
          <time dateTime={meta?.date ?? undefined}>
            {DAY.format(date)} · {TIME.format(date)}
          </time>
        )}
        {meta?.duration_s != null && meta.duration_s > 0 && (
          <span>{formatDuration(meta.duration_s)}</span>
        )}
        {meta?.app && <span className="badge">{meta.app}</span>}
        {meta?.has_audio && (
          <span className="badge badge-rec" title="A recording of this meeting is kept on this Mac">
            <span className="badge-rec-bars" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            Recording
          </span>
        )}
      </div>
    </header>
  );
}

export function RecordingMenu({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const confirmDialog = useStore((s) => s.confirmDialog);
  const refreshNotes = useStore((s) => s.refreshNotes);
  const toast = useStore((s) => s.toast);

  useEffect(() => {
    if (!open) return;
    const onClick = (evt: MouseEvent) => {
      if (evt.target instanceof Node && !wrapRef.current?.contains(evt.target)) setOpen(false);
    };
    const onKey = (evt: KeyboardEvent) => {
      if (evt.key === "Escape") setOpen(false);
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const deleteRecording = async () => {
    setOpen(false);
    const ok = await confirmDialog(
      "Delete this meeting's recording?\n\nThe summary and transcript stay. " +
        "The audio is removed from this Mac and can't be recovered.",
      { danger: true },
    );
    if (!ok) return;
    try {
      await api.deleteAudio(name);
      await refreshNotes();
      toast("Recording deleted.");
    } catch {
      toast("Could not delete the recording.", "error");
    }
  };

  return (
    <span className="export-wrap" ref={wrapRef}>
      <button
        className="btn btn-ghost btn-sm btn-icon"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name="more" />
      </button>
      {open && (
        <div className="export-menu" role="menu">
          <button role="menuitem" className="export-item export-item-danger" onClick={deleteRecording}>
            Delete recording…
          </button>
        </div>
      )}
    </span>
  );
}
