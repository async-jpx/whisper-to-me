/* Single Zustand store — the shared contract between the WS client, the API
   layer, and every component. Status is WS-authoritative: applyStatus()
   mirrors whatever the daemon reports, and optimistic flags (recordPending)
   only ever bridge the gap until the next status event. Work packages consume
   this store; its shape is not theirs to redesign. */

import { create } from "zustand";
import { api } from "./api/client";
import { IDLE_STATUS, isActive } from "./api/types";
import type { NoteMeta, SearchResult, Status, Template } from "./api/types";

/* The page shown in the main pane. "note" pairs with currentNote; every
   other view has currentNote === null. */
export type View = "home" | "live" | "note" | "chat" | "templates" | "settings";

/* The open note's two tabs. The rendered summary and the transcript stay
   mounted across switches, so playback and scroll survive a tab change. */
export type NoteTab = "summary" | "transcript";

/* Where openNote lands. Only the transcript has a timeline, so only it takes
   a second. */
export type NoteFocus = { tab: "summary" } | { tab: "transcript"; t?: number };

/* A request for the transcript tab to show second `t`: scroll to and
   highlight the line at or just before it, and cue the player there (never
   autoplay). `seq` makes asking for the same second twice a new request. */
export interface TranscriptCue {
  t: number;
  seq: number;
}

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

let toastSeq = 0;
let cueSeq = 0;

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
  noteTab: NoteTab;
  transcriptCue: TranscriptCue | null;
  /* The editor's current text while editing; null otherwise. Lets the store's
     dirty-guard see unsaved edits without owning the textarea. */
  editorDraft: string | null;
  viewArchived: boolean;
  /* Narrow windows only: the sidebar is an off-canvas drawer. */
  drawerOpen: boolean;
  // -- data caches ---------------------------------------------------------
  notes: NoteMeta[];
  archived: NoteMeta[];
  searchResults: SearchResult[] | null; // null = no active search
  templates: Template[];
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

  /* Leaves the open note (guarding unsaved edits) for a non-note view. */
  navigate(view: Exclude<View, "note">): Promise<void>;
  setDrawerOpen(open: boolean): void;
  openLive(): void;
  openNote(name: string, focus?: NoteFocus): Promise<void>;
  setNoteTab(tab: NoteTab): void;
  /* Switches the open note to its transcript at second `t`. */
  cueTranscript(t: number): void;
  forgetOpenNote(name: string): void;
  setEditing(editing: boolean, draft?: string | null): void;
  setEditorDraft(draft: string): void;
  editorDirty(): boolean;
  noteSaved(content: string): void;
  taskToggled(): Promise<void>;

  refreshNotes(): Promise<void>;
  refreshArchived(): Promise<void>;
  setSidebarTab(archived: boolean): void;
  setSearchResults(results: SearchResult[] | null): void;
  loadTemplates(): Promise<void>;
}

export const useStore = create<AppState>()((set, get) => ({
  daemonUp: false,
  status: IDLE_STATUS,
  recordPending: false,
  transcript: [],
  brief: null,
  scratchpad: "",
  view: "home",
  currentNote: null,
  currentNoteMd: null,
  editing: false,
  noteRenderSeq: 0,
  noteTab: "summary",
  transcriptCue: null,
  editorDraft: null,
  viewArchived: false,
  drawerOpen: false,
  notes: [],
  archived: [],
  searchResults: null,
  templates: [],
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
    const prev = get().status;
    // A fresh session starts with an empty scratchpad; a mid-session reconnect
    // repopulates it from the daemon (syncScratchpad) instead of wiping it.
    const fresh = !isActive(prev) && isActive(status);
    set((s) => ({
      status,
      recordPending: false, // the daemon answered; buttons follow real state
      scratchpad: fresh ? "" : s.scratchpad,
    }));
    const { view, currentNote } = get();
    if (view === "home" && isActive(status) && currentNote === null) {
      set({ view: "live" });
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
    if (!isActive(get().status)) return;
    try {
      const data = await api.getScratchpad();
      set({ scratchpad: data.content || "" });
    } catch {
      /* non-critical: keep whatever we have */
    }
  },

  async navigate(view) {
    const s = get();
    if (s.editorDirty() && !(await s.confirmDialog("Discard your unsaved edits?"))) {
      return;
    }
    set({
      view,
      currentNote: null,
      currentNoteMd: null,
      noteTab: "summary",
      transcriptCue: null,
      editing: false,
      editorDraft: null,
      drawerOpen: false,
    });
  },
  setDrawerOpen(open) {
    set({ drawerOpen: open });
  },
  openLive() {
    void get().navigate("live");
  },
  async openNote(name, focus = { tab: "summary" }) {
    const s = get();
    if (s.editorDirty() && !(await s.confirmDialog("Discard your unsaved edits?"))) {
      return;
    }
    try {
      const mdText = await api.noteContent(name);
      set({
        currentNote: name,
        currentNoteMd: mdText,
        noteTab: focus.tab,
        transcriptCue:
          focus.tab === "transcript" && focus.t !== undefined
            ? { t: focus.t, seq: ++cueSeq }
            : null,
        editing: false,
        editorDraft: null,
        view: "note",
        drawerOpen: false,
      });
    } catch {
      get().toast("Could not load that note.", "error");
    }
  },
  setNoteTab(tab) {
    set({ noteTab: tab });
  },
  cueTranscript(t) {
    set({ noteTab: "transcript", transcriptCue: { t, seq: ++cueSeq } });
  },
  forgetOpenNote(name) {
    if (get().currentNote !== name) return;
    set({
      currentNote: null,
      currentNoteMd: null,
      noteTab: "summary",
      transcriptCue: null,
      editing: false,
      editorDraft: null,
      view: "home",
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
}));
