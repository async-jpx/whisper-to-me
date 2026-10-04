/* Sidebar sections: notes bucketed by meeting day or by meeting app. Pure
   functions of (notes, now) so the grouping is testable and the Sidebar only
   renders. */

import type { NoteMeta } from "../api/types";

export type GroupMode = "day" | "app";

export interface NoteGroup {
  key: string;
  label: string;
  notes: NoteMeta[];
}

export const OTHER_APP = "Other";

/* The meeting start when the note has one, else the file's mtime. */
export function noteTime(note: NoteMeta): Date {
  const iso = note.date ?? note.modified;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? new Date(note.modified) : d;
}

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

const DAY_MS = 86_400_000;

export function dayLabel(day: Date, now: Date): string {
  // Rounded: a DST change makes one day 23 or 25 hours long.
  const ago = Math.round((startOfDay(now).getTime() - startOfDay(day).getTime()) / DAY_MS);
  if (ago === 0) return "Today";
  if (ago === 1) return "Yesterday";
  if (ago > 1 && ago < 7) return day.toLocaleDateString([], { weekday: "long" });
  return day.toLocaleDateString([], {
    month: "long",
    day: "numeric",
    ...(day.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

function byTimeDesc(a: NoteMeta, b: NoteMeta): number {
  return noteTime(b).getTime() - noteTime(a).getTime();
}

export function groupNotes(notes: NoteMeta[], mode: GroupMode, now: Date): NoteGroup[] {
  const sorted = [...notes].sort(byTimeDesc);
  const groups = new Map<string, NoteGroup>();
  for (const note of sorted) {
    let key: string;
    let label: string;
    if (mode === "day") {
      const day = startOfDay(noteTime(note));
      key = day.toISOString();
      label = dayLabel(day, now);
    } else {
      label = note.app?.trim() || OTHER_APP;
      key = label.toLowerCase();
    }
    const group = groups.get(key);
    if (group) group.notes.push(note);
    else groups.set(key, { key, label, notes: [note] });
  }
  const list = [...groups.values()];
  // Day groups are already newest first (insertion order of sorted notes).
  // App groups follow their most recent meeting, with "Other" always last.
  if (mode === "app") {
    list.sort((a, b) => {
      if (a.label === OTHER_APP) return 1;
      if (b.label === OTHER_APP) return -1;
      return 0;
    });
  }
  return list;
}

/* The time shown on a note row: a clock time within the day groups (the
   header already names the day), a short date in the app groups. */
export function rowTime(note: NoteMeta, mode: GroupMode): string {
  const t = noteTime(note);
  if (mode === "day") return t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return t.toLocaleDateString([], { month: "short", day: "numeric" });
}
