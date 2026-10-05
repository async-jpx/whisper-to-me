/* Events pushed by the daemon over WebSocket /api/events. On connect the
   server replays a status frame plus any buffered transcript lines; "brief"
   is live-only (never buffered). */

import type { Status } from "./types";

export type StatusEvent = { type: "status" } & Status;

export interface LineEvent {
  type: "line";
  stamp?: string;
  speaker?: string; // "You" | "Others" | "Speaker A"… ; absent when 1 source
  text?: string;
}

export interface PartialEvent {
  type: "partial";
  source: string;
  id: string;
  stamp: string;
  speaker: string | null;
  text: string;
}

export interface PartialClearEvent {
  type: "partial_clear";
  source: string;
  id: string;
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
  | PartialEvent
  | PartialClearEvent
  | EchoesDroppedEvent
  | BriefEvent
  | SummarizingEvent
  | SavedEvent
  | ErrorEvent;
