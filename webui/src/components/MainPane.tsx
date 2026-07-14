/* Main content area: session bar + the active view. Each pane lives in its
   own file (one work package per file — don't fold them back in here). */

import { useStore } from "../store";
import { ChatView } from "./ChatView";
import { LivePane } from "./LivePane";
import { NoteContainer } from "./NoteContainer";
import { SessionBar } from "./SessionBar";

function EmptyState() {
  const status = useStore((s) => s.status);
  return (
    <div className="empty-state">
      <p className="empty-title">Ready when you are</p>
      <p>
        Start a new meeting to record and transcribe it live —<br />
        everything stays on this machine.
      </p>
      <button
        id="empty-record-btn"
        className="btn btn-primary btn-lg"
        disabled={status.state !== "idle" && status.state !== "watching"}
        onClick={() => document.getElementById("record-btn")?.click()}
      >
        <span className="rec-glyph" aria-hidden="true"></span>
        <span>Start a new meeting</span>
      </button>
      <p className="empty-hint">…or pick a past meeting from the sidebar.</p>
    </div>
  );
}

export function MainPane() {
  const view = useStore((s) => s.view);
  return (
    <main className="main">
      <SessionBar />
      <div className="content">
        {view === "empty" && <EmptyState />}
        {view === "transcript" && <LivePane />}
        {view === "note" && <NoteContainer />}
        {view === "chat" && <ChatView />}
      </div>
    </main>
  );
}
