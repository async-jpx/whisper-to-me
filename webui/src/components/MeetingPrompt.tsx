/* The Notion-style floating meeting prompt: visible exactly while the daemon
   holds a detected meeting open for a record/ignore decision ("prompting").
   The popup hides when the next status event flips the state. */

import { useState } from "react";
import { api } from "../api/client";
import { useStore } from "../store";
import { resyncStatus } from "../ws";

export function MeetingPrompt() {
  const status = useStore((s) => s.status);
  const toast = useStore((s) => s.toast);
  const [busy, setBusy] = useState(false);

  if (status.state !== "prompting") return null;

  const respond = async (accept: boolean) => {
    setBusy(true);
    try {
      await api.watchRespond(accept);
    } catch (err) {
      toast(
        err instanceof Error && "status" in err && (err as { status: number }).status === 409
          ? "That meeting prompt has expired."
          : "Could not reach the daemon.",
        "error",
      );
      await resyncStatus();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="meeting-prompt">
      <div className="meeting-prompt-head">
        <span className="status-dot status-prompting"></span>
        <span>Meeting detected</span>
      </div>
      <div className="meeting-prompt-title">{status.title || "Meeting"}</div>
      <div className="meeting-prompt-actions">
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void respond(false)}>
          Ignore
        </button>
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => void respond(true)}>
          <span className="rec-glyph" aria-hidden="true"></span>
          <span>Record</span>
        </button>
      </div>
    </div>
  );
}
