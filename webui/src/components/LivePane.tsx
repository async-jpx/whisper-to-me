import { useRef, useEffect, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import { EditorToolbar } from "./EditorToolbar";
import { markdownKeydown } from "../lib/editing";
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
  const taRef = useRef<HTMLTextAreaElement>(null);
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
      if (liveStatus.state === "idle") return; // no session to attach notes to
      try {
        await api.putScratchpad(content);
        scratchpadErrorShownRef.current = false;
      } catch {
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

  const handleScratchpadChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setScratchpad(e.target.value);
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
              <span>Last time — </span>
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
      </div>
      <div className="scratchpad">
        <div className="scratchpad-label">Your notes shape the summary</div>
        <EditorToolbar target={taRef} />
        <textarea
          id="scratchpad"
          ref={taRef}
          spellCheck={false}
          placeholder="Type your own notes here — each point is expanded in the final summary…"
          value={scratchpad}
          onChange={handleScratchpadChange}
          onKeyDown={markdownKeydown}
        />
      </div>
    </div>
  );
}
