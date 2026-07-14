/* DTOs for the daemon's REST API. The shapes are pinned by tests/test_api.py
   and server.py — the frontend adapts to the API, never the reverse. */

export type SessionState =
  | "idle"
  | "starting"
  | "recording"
  | "stopping"
  | "watching"
  | "prompting"
  | "summarizing";

export interface Status {
  state: SessionState;
  mode?: string | null;
  title: string | null;
  started: string | null; // ISO datetime
  elapsed_s?: number | null;
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

export interface ChatResponse {
  answer: string;
  sources: ChatSource[];
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
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
