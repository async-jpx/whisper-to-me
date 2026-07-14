/* Single Zustand store — the shared contract between the WS client, the API
   layer, and every component. Status is WS-authoritative: applyStatus()
   mirrors whatever the daemon reports, and optimistic flags (recordPending)
   only ever bridge the gap until the next status event. Work packages consume
   this store; its shape is not theirs to redesign. */

import { create } from "zustand";
import { api } from "./api/client";
import type { ChatTurn, NoteMeta, SearchHit, Status, Template } from "./api/types";

export type View = "empty" | "transcript" | "note" | "chat";

export interface TranscriptLine {
  kind: "line";
  stamp: string;
  speaker: string;
  text: string;
}

export interface TranscriptNotice {
  kind: "notice";
  text: string;
}

export type TranscriptEntry = TranscriptLine | TranscriptNotice;

export interface Brief {
  title: string;
  tldr: string;
  name: string;
}

export interface Toast {
  id: number;
  message: string;
  kind: "info" | "error";
}

interface ConfirmRequest {
  message: string;
  danger: boolean;
  resolve: (ok: boolean) => void;
}

const SESSION_STATES = ["starting", "recording", "watching"];

let toastSeq = 0;

export interface AppState {
  // -- daemon status (WS-authoritative) ---------------------------------
  daemonUp: boolean;
  status: Status;
  recordPending: boolean;
  // -- live session ------------------------------------------------------
  transcript: TranscriptEntry[];
  brief: Brief | null;
  scratchpad: string;
  // -- navigation ---------------------------------------------------------
  view: View;
  currentNote: string | null;
  currentNoteMd: string | null;
  editing: boolean;
  /* Bumped when the note view must re-render its HTML (open handles itself
     via currentNote; save bumps this). A checkbox toggle updates
     currentNoteMd WITHOUT bumping it — re-rendering there would collapse the
     transcript fold and reset scroll. */
  noteRenderSeq: number;
  /* The editor's current text while editing; null otherwise. Lets the store's
     dirty-guard see unsaved edits without owning the textarea. */
  editorDraft: string | null;
  viewArchived: boolean;
  // -- data caches ---------------------------------------------------------
  notes: NoteMeta[];
  archived: NoteMeta[];
  searchResults: SearchHit[] | null; // null = no active search
  templates: Template[];
  chatHistory: ChatTurn[];
  // -- shared UI -------------------------------------------------------------
  toasts: Toast[];
  confirm: ConfirmRequest | null;

  // -- actions -----------------------------------------------------------
  toast(message: string, kind?: "info" | "error"): void;
  dismissToast(id: number): void;
  confirmDialog(message: string, opts?: { danger?: boolean }): Promise<boolean>;
  resolveConfirm(ok: boolean): void;

  applyStatus(status: Status): void;
  setDaemonUp(up: boolean): void;
  setRecordPending(pending: boolean): void;

  clearTranscript(): void;
  appendLine(line: Omit<TranscriptLine, "kind">): void;
  appendNotice(text: string): void;
  showBrief(brief: Brief): void;
  setScratchpad(content: string): void;
  syncScratchpad(): Promise<void>;

  setView(view: View): void;
  openLive(): void;
  openNote(name: string): Promise<void>;
  forgetOpenNote(name: string): void;
  setEditing(editing: boolean, draft?: string | null): void;
  setEditorDraft(draft: string): void;
  editorDirty(): boolean;
  noteSaved(content: string): void;
  taskToggled(): Promise<void>;

  refreshNotes(): Promise<void>;
  refreshArchived(): Promise<void>;
  setSidebarTab(archived: boolean): void;
  setSearchResults(results: SearchHit[] | null): void;
  loadTemplates(): Promise<void>;

  openChat(): void;
  pushChatTurn(turn: ChatTurn): void;
  popChatTurn(): void;
}

export const useStore = create<AppState>()((set, get) => ({
  daemonUp: false,
  status: { state: "idle", title: null, started: null, elapsed_s: null },
  recordPending: false,
  transcript: [],
  brief: null,
  scratchpad: "",
  view: "empty",
  currentNote: null,
  currentNoteMd: null,
  editing: false,
  noteRenderSeq: 0,
  editorDraft: null,
  viewArchived: false,
  notes: [],
  archived: [],
  searchResults: null,
  templates: [],
  chatHistory: [],
  toasts: [],
  confirm: null,

  toast(message, kind = "info") {
    const id = ++toastSeq;
    set((s) => ({ toasts: [...s.toasts, { id, message, kind }] }));
    setTimeout(() => get().dismissToast(id), 4000);
  },
  dismissToast(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
  confirmDialog(message, opts = {}) {
    return new Promise<boolean>((resolve) => {
      set({ confirm: { message, danger: !!opts.danger, resolve } });
    });
  },
  resolveConfirm(ok) {
    const req = get().confirm;
    set({ confirm: null });
    req?.resolve(ok);
  },

  applyStatus(status) {
    const prev = get().status.state;
    // A fresh session starts with an empty scratchpad; a mid-session reconnect
    // repopulates it from the daemon (syncScratchpad) instead of wiping it.
    const fresh = prev === "idle" && SESSION_STATES.includes(status.state);
    set((s) => ({
      status,
      recordPending: false, // the daemon answered; buttons follow real state
      scratchpad: fresh ? "" : s.scratchpad,
    }));
    const { view, currentNote } = get();
    if (view === "empty" && status.state !== "idle" && currentNote === null) {
      set({ view: "transcript" });
    }
  },
  setDaemonUp(up) {
    set({ daemonUp: up });
  },
  setRecordPending(pending) {
    set({ recordPending: pending });
  },

  clearTranscript() {
    set({ transcript: [], brief: null });
  },
  appendLine(line) {
    set((s) => ({ transcript: [...s.transcript, { kind: "line", ...line }] }));
  },
  appendNotice(text) {
    set((s) => ({ transcript: [...s.transcript, { kind: "notice", text }] }));
  },
  showBrief(brief) {
    set({ brief });
  },
  setScratchpad(content) {
    set({ scratchpad: content });
  },
  async syncScratchpad() {
    if (get().status.state === "idle") return;
    try {
      const data = await api.getScratchpad();
      set({ scratchpad: data.content || "" });
    } catch {
      /* non-critical: keep whatever we have */
    }
  },

  setView(view) {
    set({ view });
  },
  openLive() {
    set({ currentNote: null, view: "transcript" });
  },
  async openNote(name) {
    const s = get();
    if (s.editorDirty() && !(await s.confirmDialog("Discard your unsaved edits?"))) {
      return;
    }
    try {
      const mdText = await api.noteContent(name);
      set({
        currentNote: name,
        currentNoteMd: mdText,
        editing: false,
        editorDraft: null,
        view: "note",
      });
    } catch {
      get().toast("Could not load that note.", "error");
    }
  },
  forgetOpenNote(name) {
    if (get().currentNote !== name) return;
    set({
      currentNote: null,
      currentNoteMd: null,
      editing: false,
      editorDraft: null,
      view: "empty",
    });
  },
  setEditing(editing, draft = null) {
    set({ editing, editorDraft: editing ? (draft ?? get().currentNoteMd) : null });
  },
  setEditorDraft(draft) {
    set({ editorDraft: draft });
  },
  editorDirty() {
    const s = get();
    return s.editing && s.editorDraft !== null && s.editorDraft !== s.currentNoteMd;
  },
  noteSaved(content) {
    set((s) => ({
      currentNoteMd: content,
      editing: false,
      editorDraft: null,
      noteRenderSeq: s.noteRenderSeq + 1,
    }));
    void get().refreshNotes(); // an edited H1 changes the sidebar title
  },
  /* After a successful checkbox PATCH: refetch the raw markdown so Edit/Copy
     see the new state. The rendered DOM is left alone (checkbox order is the
     task_index contract). */
  async taskToggled() {
    const name = get().currentNote;
    if (!name) return;
    try {
      const mdText = await api.noteContent(name);
      set({ currentNoteMd: mdText });
    } catch {
      /* next openNote refetches anyway */
    }
  },

  async refreshNotes() {
    try {
      set({ notes: await api.notes() });
    } catch {
      /* non-critical; keep the previous list */
    }
  },
  async refreshArchived() {
    try {
      set({ archived: await api.archivedNotes() });
    } catch {
      /* non-critical; keep the previous list */
    }
  },
  setSidebarTab(archived) {
    set({ viewArchived: archived, searchResults: archived ? get().searchResults : null });
    if (archived) void get().refreshArchived();
    else set({ searchResults: null });
  },
  setSearchResults(results) {
    set({ searchResults: results });
  },
  async loadTemplates() {
    try {
      set({ templates: await api.templates() });
    } catch {
      /* non-critical: the Auto option alone still works */
    }
  },

  openChat() {
    set({ currentNote: null, view: "chat" });
  },
  pushChatTurn(turn) {
    set((s) => ({ chatHistory: [...s.chatHistory, turn] }));
  },
  popChatTurn() {
    set((s) => ({ chatHistory: s.chatHistory.slice(0, -1) }));
  },
}));
