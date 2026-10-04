/* DTOs for the daemon's REST API. The shapes are pinned by tests/test_api.py
   and server.py — the frontend adapts to the API, never the reverse. */

export type SessionPhase = "starting" | "recording" | "stopping" | "summarizing";
export type SessionState = "idle" | "prompting" | SessionPhase;
export type Origin = "manual" | "detected" | "simulate";

export interface MeetingPrompt {
  id: string;
  title: string;
  trigger: "zoom" | "mic";
  expires_in_s: number;
  timeout_s: number;
}

export type Status =
  | { state: "idle"; origin: null; title: null; started: null; elapsed_s: null; prompt: null }
  | {
      state: "prompting";
      origin: null;
      title: null;
      started: null;
      elapsed_s: null;
      prompt: MeetingPrompt;
    }
  | {
      state: SessionPhase;
      origin: Origin;
      title: string;
      started: string; // ISO datetime
      elapsed_s: number;
      prompt: null;
    };

export type ActiveStatus = Extract<Status, { state: SessionPhase }>;

export const IDLE_STATUS: Status = {
  state: "idle",
  origin: null,
  title: null,
  started: null,
  elapsed_s: null,
  prompt: null,
};

export function isActive(s: Status): s is ActiveStatus {
  return s.state !== "idle" && s.state !== "prompting";
}

export function canStartRecording(s: Status): boolean {
  return s.state === "idle" || s.state === "prompting";
}

export function canStop(s: Status): boolean {
  return isActive(s) && s.origin !== "simulate" && (s.state === "starting" || s.state === "recording");
}

export interface NoteMeta {
  name: string; // filename, e.g. "2026-07-13-standup.md"
  title: string;
  modified: string; // ISO datetime
  date: string | null; // ISO meeting start from frontmatter, else null
  app: string | null; // meeting app ("Zoom"), null when unknown
  has_audio: boolean;
  duration_s: number | null; // last transcript stamp, null if none
}

/* GET /api/notes/{name}/transcript: the note's transcript section, parsed
   server-side. `t` is whole seconds from the meeting start, which is also
   the position in the kept recording. */
export interface NoteLine {
  t: number;
  stamp: string; // "0:03:12"
  speaker: string | null; // null when only one source was recorded
  text: string;
}

/* GET /api/notes/{name}/audio/peaks: ~2 values per second, normalized 0..1. */
export interface AudioPeaks {
  duration_s: number;
  peaks: number[];
}

/* GET /api/search: one result per note, at most ~3 hits each. Snippets are
   bracketed by U+E000/U+E001 private-use markers; render as plain text and
   turn only the marker pairs into <mark>. */
export interface SearchHit {
  kind: "title" | "summary" | "line";
  t: number | null; // seconds from the meeting start, for "line" hits
  speaker: string | null;
  snippet: string;
}

export interface SearchResult {
  name: string;
  title: string;
  date: string | null;
  app: string | null;
  hits: SearchHit[];
}

export interface Template {
  name: string;
  title: string;
  description: string;
  builtin: boolean;
}

export interface ChatSource {
  n: number;
  name: string;
  title: string;
}

export interface ExportConfig {
  obsidian_vault: string | null;
  notion_configured: boolean;
}

export interface Settings {
  obsidian_vault: string | null;
  notion_configured: boolean;
  notion_database_id: string | null;
  /* True when a token is on file. The token itself is never sent to the page. */
  notion_token_set: boolean;
  recording: { keep_audio: boolean };
}
