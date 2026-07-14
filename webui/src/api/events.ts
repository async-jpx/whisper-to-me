/* Events pushed by the daemon over WebSocket /api/events. On connect the
   server replays a status frame plus any buffered transcript lines; "brief"
   is live-only (never buffered). */

import type { SessionState } from "./types";

export interface StatusEvent {
  type: "status";
  state: SessionState;
  title: string | null;
  started: string | null;
}

export interface LineEvent {
  type: "line";
  stamp?: string;
  speaker?: string; // "You" | "Others" | "Speaker A"… ; absent when 1 source
  text?: string;
}

export interface EchoesDroppedEvent {
  type: "echoes_dropped";
  count: number;
}

export interface BriefEvent {
  type: "brief";
  title?: string;
  tldr?: string;
  name?: string; // note filename to open
}

export interface SummarizingEvent {
  type: "summarizing";
  model: string;
}

export interface SavedEvent {
  type: "saved";
  title: string;
  name: string;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export type DaemonEvent =
  | StatusEvent
  | LineEvent
  | EchoesDroppedEvent
  | BriefEvent
  | SummarizingEvent
  | SavedEvent
  | ErrorEvent;
