/* The note's Transcript tab: what was said and when, plus the kept recording
   when there is one. Clicking a line plays from it; while playing, the
   current line is highlighted and followed until the user scrolls away. */

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import type {
  AudioPeaks,
  CommunicationAnalysis,
  MeetingCommunicationAnalysis,
  NoteLine,
} from "../api/types";
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
  selectedIdx,
  analyzing,
  analysis,
  analysisError,
  onAnalyze,
}: {
  lines: NoteLine[];
  activeIdx: number | null;
  cueIdx: number | null;
  cueSeq: number;
  onLine: ((t: number) => void) | null;
  selectedIdx: number | null;
  analyzing: boolean;
  analysis: CommunicationAnalysis | null;
  analysisError: string | null;
  onAnalyze: (index: number) => void;
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
            {(line.speaker === "You" || line.speaker === null) && (
              <div className="tl-coach-action">
                <button type="button" className="tl-analyze" onClick={() => onAnalyze(i)}
                  aria-expanded={selectedIdx === i} disabled={analyzing && selectedIdx === i}>
                  {analyzing && selectedIdx === i ? "Analyzing conversation…" : selectedIdx === i ? "Hide analysis" : "Analyze"}
                </button>
              </div>
            )}
            {selectedIdx === i && analysisError && <div className="tl-analysis" role="alert">{analysisError}</div>}
            {selectedIdx === i && analysis && (
              <div className="tl-analysis">
                <p className="tl-analysis-kicker">Communication coaching · full conversation context</p>
                <p><strong>What worked</strong>{analysis.what_worked}</p>
                <p><strong>Try improving</strong>{analysis.improvement}</p>
                <p><strong>You could say</strong><q>{analysis.try_saying}</q></p>
                <p><strong>Delivery</strong>{analysis.delivery}</p>
                {analysis.audio_metrics && <small>Audio: about {analysis.audio_metrics.approx_words_per_minute} words/min · {analysis.audio_metrics.pauses_over_0_4s} pauses over 0.4s · {analysis.audio_metrics.volume_variation} volume. Measurements use the mixed recording, so other voices may affect them.</small>}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
});

function MeetingCoaching({
  result,
  error,
  lines,
  onEvidence,
}: {
  result: MeetingCommunicationAnalysis | null;
  error: string | null;
  lines: NoteLine[];
  onEvidence: (index: number) => void;
}) {
  if (error) return <div className="tl-meeting-result" role="alert">{error}</div>;
  if (!result) return null;
  return (
    <section className="tl-meeting-result" aria-label="Meeting communication analysis">
      <p className="tl-analysis-kicker">Your communication in this meeting</p>
      <p className="tl-meeting-overview">{result.overall_read}</p>
      {result.strengths.length > 0 && (
        <div className="tl-meeting-section">
          <h3>What worked</h3>
          <ul>{result.strengths.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </div>
      )}
      {result.patterns.length > 0 && (
        <div className="tl-meeting-section">
          <h3>Habits to work on</h3>
          {result.patterns.map((item, i) => (
            <article className="tl-coaching-pattern" key={i}>
              <h4>{item.pattern}</h4>
              <p>{item.impact}</p>
              <p><strong>Try:</strong> {item.change}</p>
              {item.evidence.length > 0 && (
                <small className="tl-evidence-list">Evidence: {item.evidence.map((lineNumber, evidenceIndex) => {
                  const index = lineNumber - 1;
                  const line = lines[index];
                  return line ? (
                    <button type="button" className="tl-evidence" key={`${lineNumber}-${evidenceIndex}`}
                      onClick={() => onEvidence(index)} aria-label={`Go to transcript at ${line.stamp}`}>
                      {evidenceIndex > 0 ? ", " : ""}{formatClock(line.t)}
                    </button>
                  ) : <span key={`${lineNumber}-${evidenceIndex}`}>{evidenceIndex > 0 ? ", " : ""}line {lineNumber}</span>;
                })}</small>
              )}
            </article>
          ))}
        </div>
      )}
      {result.rewrites.length > 0 && (
        <div className="tl-meeting-section">
          <h3>How you could phrase it</h3>
          {result.rewrites.map((item, i) => {
            const sourceLine = lines[item.line_index - 1];
            return (
              <article className="tl-coaching-rewrite" key={`${item.line_index}-${i}`}>
                <small>
                  {sourceLine
                    ? <button type="button" className="tl-evidence" onClick={() => onEvidence(item.line_index - 1)}>At {formatClock(sourceLine.t)}</button>
                    : `Transcript line ${item.line_index}`}
                </small>
                <p><span>Original</span>{item.original}</p>
                <p><span>Try</span>{item.revision}</p>
                <p className="tl-rewrite-why">{item.reason}</p>
              </article>
            );
          })}
        </div>
      )}
      <div className="tl-meeting-section tl-practice">
        <h3>For next time</h3>
        <p><strong>Practice:</strong> {result.next_time.practice}</p>
        {result.next_time.steps.length > 0 && (
          <ol>{result.next_time.steps.map((step, i) => <li key={i}>{step}</li>)}</ol>
        )}
        {result.next_time.structure && <p><strong>A useful structure here:</strong> {result.next_time.structure}</p>}
      </div>
      {result.audio_metrics && (
        <small className="tl-audio-limit">
          Recording overview: {Math.floor(result.audio_metrics.duration_seconds / 60)} min
          {result.audio_metrics.approx_speaking_rate_wpm !== null &&
            ` · about ${result.audio_metrics.approx_speaking_rate_wpm} words/min in your transcript turns`}
          {` · ${result.audio_metrics.longer_low_energy_gaps} longer low-energy gaps · ${result.audio_metrics.energy_variation} energy. `}
          {result.audio_metrics.interpretation_limit}
        </small>
      )}
    </section>
  );
}

export function TranscriptTab({ name, hasAudio }: { name: string; hasAudio: boolean }) {
  const noteRenderSeq = useStore((s) => s.noteRenderSeq);
  const cue = useStore((s) => s.transcriptCue);
  const navigate = useStore((s) => s.navigate);
  const [state, setState] = useState<Lines>({ kind: "loading" });
  const [peaks, setPeaks] = useState<AudioPeaks | null>(null);
  const [following, setFollowing] = useState(true);
  const [cueIdx, setCueIdx] = useState<number | null>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [analysis, setAnalysis] = useState<CommunicationAnalysis | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [meetingAnalysis, setMeetingAnalysis] = useState<MeetingCommunicationAnalysis | null>(null);
  const [meetingAnalyzing, setMeetingAnalyzing] = useState(false);
  const [meetingError, setMeetingError] = useState<string | null>(null);
  const meetingRequest = useRef(0);
  const analysisRequest = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const handledCueRef = useRef(0);

  const lines = state.kind === "ready" ? state.lines : null;
  const lastT = lines?.at(-1)?.t ?? 0;
  const player = usePlayer(peaks?.duration_s ?? lastT, hasAudio);
  const { playing, time, playFrom, seek } = player;

  // Re-read on open and after a save (an edit can change the transcript).
  useEffect(() => {
    let live = true;
    analysisRequest.current += 1;
    meetingRequest.current += 1;
    setSelectedIdx(null);
    setAnalysis(null);
    setAnalysisError(null);
    setAnalyzing(false);
    setMeetingAnalysis(null);
    setMeetingAnalyzing(false);
    setMeetingError(null);
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

  const onAnalyze = (index: number) => {
    if (selectedIdx === index) {
      analysisRequest.current += 1;
      setSelectedIdx(null);
      setAnalyzing(false);
      return;
    }
    const request = ++analysisRequest.current;
    setSelectedIdx(index);
    setAnalysis(null);
    setAnalysisError(null);
    setAnalyzing(true);
    api.analyzeLine(name, index)
      .then((result) => { if (request === analysisRequest.current) setAnalysis(result); })
      .catch((error: unknown) => {
        if (request === analysisRequest.current) setAnalysisError(
          error instanceof Error ? error.message : "Analysis could not be completed.",
        );
      })
      .finally(() => { if (request === analysisRequest.current) setAnalyzing(false); });
  };

  const onAnalyzeMeeting = () => {
    if (meetingAnalysis) {
      meetingRequest.current += 1;
      setMeetingAnalysis(null);
      setMeetingError(null);
      return;
    }
    const request = ++meetingRequest.current;
    setMeetingAnalyzing(true);
    setMeetingError(null);
    api.analyzeMeeting(name)
      .then((result) => { if (request === meetingRequest.current) setMeetingAnalysis(result); })
      .catch((error: unknown) => {
        if (request === meetingRequest.current) setMeetingError(
          error instanceof Error ? error.message : "Meeting analysis could not be completed.",
        );
      })
      .finally(() => { if (request === meetingRequest.current) setMeetingAnalyzing(false); });
  };

  const goToEvidence = (index: number) => {
    const line = lines?.[index];
    if (!line) return;
    setCueIdx(index);
    scrollToLine(index);
    if (hasAudio) seek(line.t);
  };

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
          <div className="tl-meeting-action">
            <button className="btn btn-ghost btn-sm" type="button"
              onClick={onAnalyzeMeeting} disabled={meetingAnalyzing}>
              {meetingAnalyzing ? "Analyzing the full meeting…" : meetingAnalysis ? "Hide meeting analysis" : "Analyze full meeting"}
            </button>
            <span>Patterns, specific rewrites, and a practice plan</span>
            {(meetingAnalysis || meetingError) && (
              <MeetingCoaching result={meetingAnalysis} error={meetingError} lines={lines}
                onEvidence={goToEvidence} />
            )}
          </div>
        )}
        {lines && lines.length > 0 && (
          <LineList
            lines={lines}
            activeIdx={activeIdx}
            cueIdx={cueIdx}
            cueSeq={cue?.seq ?? 0}
            onLine={onLine}
            selectedIdx={selectedIdx}
            analyzing={analyzing}
            analysis={analysis}
            analysisError={analysisError}
            onAnalyze={onAnalyze}
          />
        )}
      </div>
    </div>
  );
}
