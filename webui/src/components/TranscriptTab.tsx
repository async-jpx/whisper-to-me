/* The note's Transcript tab: what was said and when, plus the kept recording
   when there is one. Clicking a line plays from it; while playing, the
   current line is highlighted and followed until the user scrolls away. */

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import type { AudioPeaks, NoteLine } from "../api/types";
import { formatClock, spokenClock } from "../lib/time";
import { AudioPlayer, usePlayer } from "./AudioPlayer";

type Lines = { kind: "loading" } | { kind: "failed" } | { kind: "ready"; lines: NoteLine[] };

/* Index of the last line starting at or before `t`; 0 before the first. */
function lineAt(lines: NoteLine[], t: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let found = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((lines[mid]?.t ?? 0) <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

function chipClass(speaker: string): string {
  if (speaker === "You") return "chip chip-you";
  if (speaker === "Others") return "chip chip-others";
  const letter = speaker.match(/^Speaker ([A-Z])$/)?.[1];
  return letter ? `chip chip-spk-${(letter.charCodeAt(0) - 65) % 4}` : "chip chip-others";
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

const LineList = memo(function LineList({
  lines,
  activeIdx,
  cueIdx,
  cueSeq,
  onLine,
}: {
  lines: NoteLine[];
  activeIdx: number | null;
  cueIdx: number | null;
  cueSeq: number;
  onLine: ((t: number) => void) | null;
}) {
  return (
    <ol className="tl">
      {lines.map((line, i) => {
        const prev = lines[i - 1];
        const showChip = line.speaker !== null && line.speaker !== prev?.speaker;
        const body = (
          <>
            <span className="tl-time">{formatClock(line.t)}</span>
            <span className="tl-body">
              {showChip && line.speaker && <span className={chipClass(line.speaker)}>{line.speaker}</span>}
              <span className="tl-text">{line.text}</span>
            </span>
          </>
        );
        const cls =
          "tl-line" +
          (i === activeIdx ? " is-active" : "") +
          (i === cueIdx ? " is-cued" : "") +
          (showChip && i > 0 ? " is-turn" : "");
        return (
          <li key={i} data-line={i}>
            {onLine ? (
              <button
                // Re-keyed per cue so the highlight animation restarts.
                key={i === cueIdx ? `cue-${cueSeq}` : "line"}
                type="button"
                className={cls}
                aria-current={i === activeIdx ? "true" : undefined}
                aria-label={`Play from ${spokenClock(line.t)}. ${line.speaker ? line.speaker + ": " : ""}${line.text}`}
                onClick={() => onLine(line.t)}
              >
                {body}
              </button>
            ) : (
              <div key={i === cueIdx ? `cue-${cueSeq}` : "line"} className={cls + " is-static"}>
                {body}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
});

export function TranscriptTab({ name, hasAudio }: { name: string; hasAudio: boolean }) {
  const noteRenderSeq = useStore((s) => s.noteRenderSeq);
  const cue = useStore((s) => s.transcriptCue);
  const navigate = useStore((s) => s.navigate);
  const [state, setState] = useState<Lines>({ kind: "loading" });
  const [peaks, setPeaks] = useState<AudioPeaks | null>(null);
  const [following, setFollowing] = useState(true);
  const [cueIdx, setCueIdx] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const handledCueRef = useRef(0);

  const lines = state.kind === "ready" ? state.lines : null;
  const lastT = lines?.at(-1)?.t ?? 0;
  const player = usePlayer(peaks?.duration_s ?? lastT);
  const { playing, time, playFrom, seek } = player;

  // Re-read on open and after a save (an edit can change the transcript).
  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    api
      .transcript(name)
      .then((l) => live && setState({ kind: "ready", lines: l }))
      .catch(() => live && setState({ kind: "failed" }));
    return () => {
      live = false;
    };
  }, [name, noteRenderSeq]);

  useEffect(() => {
    if (!hasAudio) return;
    let live = true;
    api
      .audioPeaks(name)
      .then((p) => live && setPeaks(p))
      .catch(() => live && setPeaks(null)); // flat bars; the audio itself still plays
    return () => {
      live = false;
    };
  }, [name, hasAudio]);

  const activeIdx =
    hasAudio && lines && lines.length > 0 && (playing || time > 0) ? lineAt(lines, time) : null;

  const scrollToLine = (idx: number) => {
    listRef.current
      ?.querySelector(`[data-line="${idx}"]`)
      ?.scrollIntoView({ block: "center", behavior: scrollBehavior() });
  };

  // A manual scroll while playing stops the follow; the "Current line"
  // button, a line click or a fresh play resumes it. Programmatic
  // scrollIntoView fires no wheel/touch/key events, so it never trips this.
  useEffect(() => {
    if (!playing) return;
    const stop = () => setFollowing(false);
    const onKey = (evt: KeyboardEvent) => {
      if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End"].includes(evt.key)) {
        const inSlider = evt.target instanceof Element && evt.target.closest(".wave");
        if (!inSlider) stop();
      }
    };
    window.addEventListener("wheel", stop, { passive: true });
    window.addEventListener("touchmove", stop, { passive: true });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("wheel", stop);
      window.removeEventListener("touchmove", stop);
      window.removeEventListener("keydown", onKey);
    };
  }, [playing]);

  useEffect(() => {
    if (playing) setFollowing(true);
  }, [playing]);

  useEffect(() => {
    if (activeIdx !== null && playing && following) scrollToLine(activeIdx);
  }, [activeIdx, following]);

  // openNote(name, { tab: "transcript", t }) / summary stamp links.
  useEffect(() => {
    if (!cue || !lines || cue.seq === handledCueRef.current) return;
    handledCueRef.current = cue.seq;
    const idx = lines.length > 0 ? lineAt(lines, cue.t) : null;
    setCueIdx(idx);
    if (idx !== null) requestAnimationFrame(() => scrollToLine(idx));
    if (hasAudio) seek(cue.t);
  }, [cue, lines]);

  const onLine = useMemo(
    () =>
      hasAudio
        ? (t: number) => {
            setFollowing(true);
            setCueIdx(null);
            playFrom(t);
          }
        : null,
    [hasAudio, playFrom],
  );

  return (
    <div className="transcript-tab">
      {hasAudio ? (
        <div className="player-dock">
          <AudioPlayer
            src={api.audioUrl(name)}
            peaks={peaks}
            player={player}
            following={following}
            onFollow={() => {
              setFollowing(true);
              if (activeIdx !== null) scrollToLine(activeIdx);
            }}
          />
        </div>
      ) : (
        <p className="tl-hint">
          No recording was kept for this meeting. To replay future meetings, turn on{" "}
          <button className="link" onClick={() => void navigate("settings")}>
            Keep recordings in Settings
          </button>
          .
        </p>
      )}
      <div ref={listRef}>
        {state.kind === "loading" && <p className="tl-empty">Loading the transcript…</p>}
        {state.kind === "failed" && <p className="tl-empty">Could not load the transcript.</p>}
        {lines && lines.length === 0 && <p className="tl-empty">Nothing was transcribed in this meeting.</p>}
        {lines && lines.length > 0 && (
          <LineList
            lines={lines}
            activeIdx={activeIdx}
            cueIdx={cueIdx}
            cueSeq={cue?.seq ?? 0}
            onLine={onLine}
          />
        )}
      </div>
    </div>
  );
}
