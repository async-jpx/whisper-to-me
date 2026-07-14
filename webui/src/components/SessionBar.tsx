import { useEffect, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import { resyncStatus } from "../ws";
import type { ApiError } from "../api/client";

export function SessionBar() {
  const status = useStore((s) => s.status);
  const recordPending = useStore((s) => s.recordPending);
  const templates = useStore((s) => s.templates);
  const toast = useStore((s) => s.toast);
  const setView = useStore((s) => s.setView);
  const clearTranscript = useStore((s) => s.clearTranscript);
  const openLive = useStore((s) => s.openLive);
  const setRecordPending = useStore((s) => s.setRecordPending);

  const [title, setTitle] = useState("");
  const [template, setTemplate] = useState("");
  const [elapsed, setElapsed] = useState("");

  // Format elapsed time: h>0 ? "h:mm:ss" : "m:ss" with 2-digit padding
  function formatElapsed(totalSeconds: number): string {
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = Math.floor(totalSeconds % 60);

    if (hours > 0) {
      return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  // Elapsed ticker: only runs while recording
  useEffect(() => {
    if (status.state !== "recording" || !status.started) {
      setElapsed("");
      return;
    }

    const tick = () => {
      const startedMs = Date.parse(status.started!);
      if (!Number.isNaN(startedMs)) {
        const elapsed = (Date.now() - startedMs) / 1000;
        setElapsed(formatElapsed(elapsed));
      }
    };

    tick(); // initial render
    const timer = setInterval(tick, 500);
    return () => clearInterval(timer);
  }, [status.state, status.started]);

  // Status text labels
  const statusLabels: Record<string, string> = {
    idle: "Ready",
    starting: "Starting the recorder…",
    recording: status.title || "Recording",
    stopping: "Finishing — transcribing the last audio…",
    watching: "Watching for meetings…",
    prompting: status.title ? `Meeting detected — ${status.title}` : "Meeting detected",
    summarizing: "Summarizing…",
  };
  const statusText = statusLabels[status.state] || status.state;

  // Record button state
  const busy = status.state === "stopping" || status.state === "summarizing";
  const isStop = status.state === "recording" || status.state === "starting";
  const recordDisabled = busy || status.state === "prompting" || recordPending;
  const recordLabel = busy ? (status.state === "stopping" ? "Finishing…" : "Summarizing…") : isStop ? "Stop" : "New meeting";

  // Watch button state
  const isWatching = status.state === "watching" || status.state === "prompting";
  const watchDisabled = status.state !== "idle" && status.state !== "watching" && status.state !== "prompting";
  const watchLabel = isWatching ? "Stop Watching" : "Watch";

  // Settable fields (title/template)
  const settable = status.state === "idle" || status.state === "watching";

  async function onRecordClick() {
    if (recordPending) return;

    setRecordPending(true);

    // Failsafe: if no status event arrives (dead socket), unlock and resync
    // rather than leaving the button disabled forever.
    setTimeout(() => {
      if (useStore.getState().recordPending) {
        useStore.getState().setRecordPending(false);
        void resyncStatus();
      }
    }, 5000);

    if (status.state === "recording" || status.state === "starting") {
      // Stop flow
      try {
        await api.recordStop();
      } catch (err) {
        setRecordPending(false);
        toast("Could not stop the recording.", "error");
        await resyncStatus();
      }
    } else {
      // Start flow: clear + switch view BEFORE the request
      clearTranscript();
      openLive();

      try {
        await api.recordStart(title.trim() || null, template || null);
      } catch (err) {
        setRecordPending(false);
        const statusErr = err as ApiError;
        if (statusErr.status === 409) {
          toast("The daemon is busy with another session — try again in a moment.", "error");
        } else {
          toast("Could not start recording.", "error");
        }
        await resyncStatus();
        if (useStore.getState().status.state === "idle") {
          setView("empty");
        }
      }
    }
  }

  async function onWatchClick() {
    try {
      if (status.state === "watching" || status.state === "prompting") {
        await api.watchStop();
      } else {
        await api.watchStart();
      }
    } catch (err) {
      const statusErr = err as ApiError;
      if (statusErr.status === 409) {
        toast("Already busy — can't do that right now.", "error");
      } else {
        toast("Could not reach the daemon.", "error");
      }
      await resyncStatus();
    }
  }

  return (
    <div className="session-bar">
      <div className="status">
        <span className={"status-dot status-" + status.state}></span>
        <span className="status-text">{statusText}</span>
        <span className="elapsed">{elapsed}</span>
      </div>
      <div className="controls">
        <input
          type="text"
          className="title-input"
          placeholder="Meeting title (optional)"
          maxLength={120}
          disabled={!settable}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <select
          className="template-select"
          disabled={!settable}
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
        >
          <option value="">Auto template</option>
          {templates.map((t) => (
            <option key={t.name} value={t.name}>
              {t.description || t.name}
            </option>
          ))}
        </select>
        <button
          id="watch-btn"
          className={"btn btn-ghost" + (isWatching ? " is-active" : "")}
          disabled={watchDisabled}
          onClick={onWatchClick}
        >
          {watchLabel}
        </button>
        <button
          id="record-btn"
          className={
            "btn btn-primary" +
            (isStop ? " is-stop" : "") +
            (busy || recordPending ? " is-busy" : "")
          }
          disabled={recordDisabled}
          onClick={onRecordClick}
        >
          <span className="rec-glyph" aria-hidden="true"></span>
          <span id="record-btn-label">{recordLabel}</span>
        </button>
      </div>
    </div>
  );
}
