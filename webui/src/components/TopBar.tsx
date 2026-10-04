/* The main pane's header: drawer toggle (narrow windows), the daemon's
   session status, and Stop while a recording can be stopped. The web UI
   never answers a meeting prompt; it only names it. */

import { useEffect, useState } from "react";
import { useStore } from "../store";
import { canStop, type Status } from "../api/types";
import { stopRecording } from "../lib/record";
import { formatClock } from "../lib/time";
import { Icon } from "./Icons";
import { Logo } from "./Logo";

function statusText(status: Status): string | null {
  switch (status.state) {
    case "idle":
      return null;
    case "prompting":
      return `Meeting detected: ${status.prompt.title}`;
    case "starting":
      return "Starting the recorder…";
    case "recording":
      return status.title || "Recording";
    case "stopping":
      return "Finishing the transcript…";
    case "summarizing":
      return "Writing your notes…";
  }
}

/* Anchored on the daemon's elapsed_s, not on `started`: that is a naive
   local timestamp, and parsing it in a page whose time zone differs from
   the daemon's skews the clock by the offset. */
function useElapsed(status: Status): string {
  const [elapsed, setElapsed] = useState("");
  const elapsedS = status.state === "recording" ? status.elapsed_s : null;
  useEffect(() => {
    if (elapsedS === null) {
      setElapsed("");
      return;
    }
    const startedMs = Date.now() - elapsedS * 1000;
    const tick = () => setElapsed(formatClock((Date.now() - startedMs) / 1000));
    tick();
    const timer = setInterval(tick, 500);
    return () => clearInterval(timer);
  }, [elapsedS]);
  return elapsed;
}

export function TopBar() {
  const status = useStore((s) => s.status);
  const recordPending = useStore((s) => s.recordPending);
  const setDrawerOpen = useStore((s) => s.setDrawerOpen);
  const view = useStore((s) => s.view);
  const openLive = useStore((s) => s.openLive);
  const elapsed = useElapsed(status);
  const text = statusText(status);

  return (
    <header className="topbar">
      <button className="icon-btn topbar-menu" aria-label="Open sidebar" onClick={() => setDrawerOpen(true)}>
        <Icon name="menu" />
      </button>
      <span className="topbar-brand">
        <Logo size={22} />
        <span className="brand">Hush</span>
      </span>
      {text && (
        <button
          className="status-pill"
          onClick={view === "live" ? undefined : openLive}
          disabled={status.state === "prompting"}
          title={text}
        >
          <span className={"status-dot status-" + status.state} />
          <span className="status-text">{text}</span>
          {elapsed && <span className="elapsed">{elapsed}</span>}
        </button>
      )}
      <span className="topbar-spacer" />
      {canStop(status) && (
        <button
          className="btn btn-stop"
          disabled={recordPending}
          onClick={() => void stopRecording()}
        >
          <span className="rec-glyph is-stop" aria-hidden="true" />
          <span>Stop</span>
        </button>
      )}
    </header>
  );
}
