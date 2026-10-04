import { useRef, useEffect, useState } from "react";
import { useStore } from "../store";
import { api, ApiError } from "../api/client";
import { isActive } from "../api/types";
import { EditorToolbar } from "./EditorToolbar";
import { MarkdownEditor, type ReactCodeMirrorRef } from "./MarkdownEditor";
import { Icon } from "./Icons";

export function LivePane() {
  const transcript = useStore((s) => s.transcript);
  const brief = useStore((s) => s.brief);
  const scratchpad = useStore((s) => s.scratchpad);
  const setScratchpad = useStore((s) => s.setScratchpad);
  const toast = useStore((s) => s.toast);
  const openNote = useStore((s) => s.openNote);

  const scrollRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const cmRef = useRef<ReactCodeMirrorRef>(null);
  const scratchpadTimerRef = useRef<number | null>(null);
  const scratchpadErrorShownRef = useRef(false);

  const [dismissedBrief, setDismissedBrief] = useState<Brief | null>(null);

  type Brief = typeof brief;
  const showBriefCard = brief && brief !== dismissedBrief;

  // Handle scroll — set autoScroll false when user scrolls up
  const handleScroll = () => {
    if (!scrollRef.current) return;
    const { scrollHeight, scrollTop, clientHeight } = scrollRef.current;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    autoScrollRef.current = distanceFromBottom < 40;
  };

  // Auto-scroll when transcript changes (if autoScroll is enabled)
  useEffect(() => {
    if (autoScrollRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcript]);

  // Reset dismissed brief when store.brief changes
  useEffect(() => {
    setDismissedBrief(null);
  }, [brief]);

  // Scratchpad save with debounce
  const scheduleSave = () => {
    if (scratchpadTimerRef.current) {
      clearTimeout(scratchpadTimerRef.current);
    }
    scratchpadTimerRef.current = window.setTimeout(async () => {
      // Read through the store at fire time: the closure's `scratchpad` is
      // one keystroke stale (state hadn't re-rendered when this scheduled).
      const { status: liveStatus, scratchpad: content } = useStore.getState();
      if (!isActive(liveStatus)) return; // no session to attach notes to
      try {
        await api.putScratchpad(content);
        scratchpadErrorShownRef.current = false;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) return;
        if (!scratchpadErrorShownRef.current) {
          toast("Couldn't save your notes to the session.", "error");
          scratchpadErrorShownRef.current = true;
        }
      }
    }, 750);
  };

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (scratchpadTimerRef.current) {
        clearTimeout(scratchpadTimerRef.current);
      }
    };
  }, []);

  const handleScratchpadChange = (text: string) => {
    setScratchpad(text);
    scheduleSave();
  };

  const handleDismissBrief = () => {
    setDismissedBrief(brief);
  };

  const handleOpenNote = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (brief?.name) {
      await openNote(brief.name);
    }
  };

  return (
    <div className="live-pane">
      <div className="transcript" ref={scrollRef} onScroll={handleScroll}>
        {showBriefCard && (
          <div className="brief-card">
            <div className="brief-head">
              <Icon name="clipboard" />
              <span>Last time</span>
              <strong>{brief.title}</strong>
              <button
                className="brief-dismiss"
                onClick={handleDismissBrief}
                title="Dismiss"
                aria-label="Dismiss"
                type="button"
              >
                <Icon name="close" />
              </button>
            </div>
            {brief.tldr && <div className="brief-tldr">{brief.tldr}</div>}
            {brief.name && (
              <a className="brief-open" href="#" onClick={handleOpenNote}>
                Open note
              </a>
            )}
          </div>
        )}
        {transcript.map((entry, i) =>
          entry.kind === "line" ? (
            <div className="t-line" key={i}>
              <span className="t-stamp">{entry.stamp}</span>
              {entry.speaker && (
                <span
                  className={
                    "chip " + (entry.speaker === "You" ? "chip-you" : "chip-others")
                  }
                >
                  {entry.speaker}
                </span>
              )}
              <span className="t-text">{entry.text}</span>
            </div>
          ) : (
            <div className="t-notice" key={i}>
              {entry.text}
            </div>
          ),
        )}
        {transcript.length === 0 && (
          <p className="t-empty">Listening. Lines appear here as people speak.</p>
        )}
      </div>
      <div className="scratchpad">
        <div className="scratchpad-label">Your notes</div>
        <div className="scratchpad-frame">
          <MarkdownEditor
            ref={cmRef}
            className="scratchpad-editor"
            value={scratchpad}
            onChange={handleScratchpadChange}
            placeholder="Jot down what matters. Each point is expanded in the summary."
          />
          <EditorToolbar target={cmRef} />
        </div>
      </div>
    </div>
  );
}
