/* Coaching inside the note's Transcript tab: the per-line "Analyze" panel and
   the full-meeting review. The latest meeting review is saved by the daemon
   and shown again when the transcript reopens. */

import { useEffect, useMemo, useRef, useState } from "react";
import type { NoteLine } from "../api/types";
import { formatClock } from "../lib/time";
import { coachingApi, type CommunicationAnalysis, type MeetingAudio, type SavedReview } from "./api";
import "./coaching.css";

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export interface LineCoaching {
  selectedIdx: number | null;
  analyzing: boolean;
  analysis: CommunicationAnalysis | null;
  error: string | null;
  toggle: (index: number) => void;
}

/* `resetKey` changes on note open / save; any in-flight answer is dropped. */
export function useLineCoaching(name: string, resetKey: unknown): LineCoaching {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [analysis, setAnalysis] = useState<CommunicationAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const request = useRef(0);

  useEffect(() => {
    request.current += 1;
    setSelectedIdx(null);
    setAnalysis(null);
    setError(null);
    setAnalyzing(false);
  }, [name, resetKey]);

  const toggle = (index: number) => {
    if (selectedIdx === index) {
      request.current += 1;
      setSelectedIdx(null);
      setAnalyzing(false);
      return;
    }
    const id = ++request.current;
    setSelectedIdx(index);
    setAnalysis(null);
    setError(null);
    setAnalyzing(true);
    coachingApi.analyzeLine(name, index)
      .then((result) => { if (id === request.current) setAnalysis(result); })
      .catch((err: unknown) => {
        if (id === request.current) setError(message(err, "Analysis could not be completed."));
      })
      .finally(() => { if (id === request.current) setAnalyzing(false); });
  };

  // Stable between state changes so the memoized line list skips playback ticks.
  return useMemo(() => ({ selectedIdx, analyzing, analysis, error, toggle }),
    [selectedIdx, analyzing, analysis, error, name]);
}

export function LineCoach({ coach, index }: { coach: LineCoaching; index: number }) {
  const selected = coach.selectedIdx === index;
  const { analysis } = coach;
  return (
    <>
      <div className="tl-coach-action">
        <button type="button" className="tl-analyze" onClick={() => coach.toggle(index)}
          aria-expanded={selected} disabled={coach.analyzing && selected}>
          {coach.analyzing && selected ? "Analyzing conversation…" : selected ? "Hide analysis" : "Analyze"}
        </button>
      </div>
      {selected && coach.error && <div className="tl-analysis" role="alert">{coach.error}</div>}
      {selected && analysis && (
        <div className="tl-analysis">
          <p className="tl-analysis-kicker">Communication coaching · full conversation context</p>
          <p><strong>What worked</strong>{analysis.what_worked}</p>
          <p><strong>Try improving</strong>{analysis.improvement}</p>
          <p><strong>You could say</strong><q>{analysis.try_saying}</q></p>
          <p><strong>Delivery</strong>{analysis.delivery}</p>
          {analysis.audio_metrics && <small>Audio: about {analysis.audio_metrics.approx_words_per_minute} words/min · {analysis.audio_metrics.pauses_over_0_4s} pauses over 0.4s · {analysis.audio_metrics.volume_variation} volume. {analysis.audio_metrics.source === "your microphone" ? "Measured on your own microphone." : "Measurements use the mixed recording, so other voices may affect them."}</small>}
        </div>
      )}
    </>
  );
}

function AudioSummary({ audio }: { audio: MeetingAudio }) {
  if (audio.source === "your microphone") {
    return (
      <small className="tl-audio-limit">
        Your voice: about {audio.speaking_rate_wpm} words/min · {audio.pauses_per_minute} pauses/min
        {audio.pitch_range_semitones !== null &&
          ` · pitch spread ${audio.pitch_range_semitones} semitones around ${audio.pitch_median_hz} Hz`}
        {` · loudness range ${audio.loudness_range_db} dB. `}
        {audio.interpretation_limit}
      </small>
    );
  }
  return (
    <small className="tl-audio-limit">
      Recording overview: {Math.floor(audio.duration_seconds / 60)} min
      {audio.approx_speaking_rate_wpm !== null &&
        ` · about ${audio.approx_speaking_rate_wpm} words/min in your transcript turns`}
      {` · ${audio.longer_low_energy_gaps} longer low-energy gaps · ${audio.energy_variation} energy. `}
      {audio.interpretation_limit}
    </small>
  );
}

function MeetingReview({
  saved,
  lines,
  onEvidence,
}: {
  saved: SavedReview;
  lines: NoteLine[];
  onEvidence: (index: number) => void;
}) {
  const result = saved.review;
  return (
    <section className="tl-meeting-result" aria-label="Meeting communication analysis">
      <p className="tl-analysis-kicker">
        Your communication in this meeting · reviewed {new Date(saved.analyzed_at).toLocaleDateString()}
      </p>
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
      {result.audio_metrics && <AudioSummary audio={result.audio_metrics} />}
    </section>
  );
}

export function MeetingCoaching({
  name,
  resetKey,
  lines,
  onEvidence,
}: {
  name: string;
  resetKey: unknown;
  lines: NoteLine[];
  onEvidence: (index: number) => void;
}) {
  const [saved, setSaved] = useState<SavedReview | null>(null);
  const [shown, setShown] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);

  useEffect(() => {
    const id = ++request.current;
    setSaved(null);
    setShown(false);
    setAnalyzing(false);
    setError(null);
    coachingApi.savedReview(name)
      .then((record) => { if (id === request.current) setSaved(record); })
      .catch(() => {}); // 404 = never reviewed
  }, [name, resetKey]);

  const analyze = () => {
    const id = ++request.current;
    setAnalyzing(true);
    setError(null);
    coachingApi.analyzeMeeting(name)
      .then((record) => {
        if (id !== request.current) return;
        setSaved(record);
        setShown(true);
      })
      .catch((err: unknown) => {
        if (id === request.current) setError(message(err, "Meeting analysis could not be completed."));
      })
      .finally(() => { if (id === request.current) setAnalyzing(false); });
  };

  return (
    <div className="tl-meeting-action">
      {saved && (
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => setShown(!shown)}>
          {shown ? "Hide meeting review" : "Show meeting review"}
        </button>
      )}
      <button className="btn btn-ghost btn-sm" type="button" onClick={analyze} disabled={analyzing}>
        {analyzing ? "Analyzing the full meeting…" : saved ? "Re-analyze" : "Analyze full meeting"}
      </button>
      <span>Patterns, specific rewrites, and a practice plan · saved to your progress</span>
      {error && <div className="tl-meeting-result" role="alert">{error}</div>}
      {shown && saved && <MeetingReview saved={saved} lines={lines} onEvidence={onEvidence} />}
    </div>
  );
}
