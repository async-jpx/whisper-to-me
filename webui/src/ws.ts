/* WebSocket client + boot loop — a plain module (not a hook) so reconnect
   timers and the daemon-down retry loop live outside React's lifecycle. The
   self-heal model mirrors the old UI exactly: WS events are authoritative,
   resync-on-focus repairs missed events, and a dead daemon sends the page
   back to the boot loop (retry card) instead of leaving a stale session bar. */

import { api } from "./api/client";
import type { DaemonEvent } from "./api/events";
import { useStore } from "./store";

let retryMs = 1000;
let started = false;

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/events`;
}

function handleEvent(evt: DaemonEvent): void {
  const s = useStore.getState();
  switch (evt.type) {
    case "status":
      s.applyStatus({
        state: evt.state,
        title: evt.title,
        started: evt.started,
        elapsed_s: s.status.elapsed_s,
      });
      break;
    case "line":
      s.appendLine({
        stamp: evt.stamp || "",
        speaker: evt.speaker || "",
        text: evt.text || "",
      });
      break;
    case "echoes_dropped":
      s.appendNotice(`${evt.count} echoed line${evt.count === 1 ? "" : "s"} dropped`);
      break;
    case "brief":
      s.showBrief({ title: evt.title || "", tldr: evt.tldr || "", name: evt.name || "" });
      break;
    case "summarizing":
      s.toast(`Summarizing with ${evt.model}…`);
      break;
    case "saved":
      s.toast(`Saved “${evt.title}”`);
      // Don't steal the view (or pop a confirm) out from under an edit.
      void s.refreshNotes().then(() => {
        if (!useStore.getState().editing) void useStore.getState().openNote(evt.name);
      });
      break;
    case "error":
      s.toast(evt.message, "error");
      break;
  }
}

function connectEvents(): void {
  const ws = new WebSocket(wsUrl());

  ws.addEventListener("open", () => {
    retryMs = 1000;
    const s = useStore.getState();
    // The server replays the current status + buffered transcript lines on
    // connect, so a clean slate never loses a mid-session join.
    s.clearTranscript();
    void s.refreshNotes();
    api
      .status()
      .then((status) => {
        useStore.getState().applyStatus(status);
        void useStore.getState().syncScratchpad();
      })
      .catch(() => {});
  });

  ws.addEventListener("message", (msg) => {
    let evt: DaemonEvent;
    try {
      evt = JSON.parse(msg.data);
    } catch {
      return;
    }
    handleEvent(evt);
  });

  ws.addEventListener("close", () => {
    // A dropped socket with a live daemon just reconnects; a dead daemon
    // sends the page back to the boot loop so the retry card shows.
    api
      .status()
      .then(() => {
        setTimeout(connectEvents, retryMs);
        retryMs = Math.min(retryMs * 2, 15000);
      })
      .catch(() => {
        useStore.getState().setDaemonUp(false);
        setTimeout(boot, 2000);
      });
  });
  ws.addEventListener("error", () => ws.close());
}

/* Re-fetch the daemon's status and apply it — the self-heal path for missed
   WS events (sleeping laptop, failed request). Exported for the record/watch
   error paths and the recordPending failsafe. */
export async function resyncStatus(): Promise<void> {
  try {
    useStore.getState().applyStatus(await api.status());
  } catch {
    /* daemon unreachable: the WS close handler shows the retry card */
  }
}

async function boot(): Promise<void> {
  const s = useStore.getState();
  try {
    const status = await api.status();
    s.setDaemonUp(true);
    s.applyStatus(status);
    await s.refreshNotes();
    void s.refreshArchived();
    await s.syncScratchpad();
    await s.loadTemplates();
    applyNoteHash();
    connectEvents();
  } catch {
    s.setDaemonUp(false);
    setTimeout(boot, 2000);
  }
}

/* Deep links: #note=<name>, set by the desktop tray ("Open last note"). The
   tray clears the hash first so re-opening the same note still fires. */
function applyNoteHash(): void {
  const m = location.hash.match(/^#note=(.+)$/);
  if (m && m[1]) void useStore.getState().openNote(decodeURIComponent(m[1]));
}

export function startApp(): void {
  if (started) return; // React StrictMode double-invokes effects in dev
  started = true;
  window.addEventListener("hashchange", applyNoteHash);
  window.addEventListener("focus", () => void resyncStatus());
  void boot();
}
