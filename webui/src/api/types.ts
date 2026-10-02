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
}

export interface SearchHit extends NoteMeta {
  /* Snippet hits are bracketed by U+E000/U+E001 private-use markers; render
     as plain text and turn only the marker pairs into <mark>. */
  snippet: string;
}

export interface Template {
  name: string;
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
}
