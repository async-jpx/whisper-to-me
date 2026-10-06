/* Coaching progress: speaking stats for every meeting you spoke in (computed
   from transcripts, no model call), recent-vs-earlier averages, the practice
   focus each saved meeting review set, and a per-meeting table. */

import { useEffect, useState } from "react";
import { useStore } from "../store";
import { coachingApi, type Dashboard, type TrendMetric } from "./api";
import "./coaching.css";

type Load = { kind: "loading" } | { kind: "failed" } | { kind: "ready"; data: Dashboard };
type Meeting = Dashboard["meetings"][number];

const SPARK_POINTS = 20;

type Metric = { key: TrendMetric; label: string; hint: string; format: (v: number) => string };

const METRICS: Metric[] = [
  { key: "talk_share", label: "Talk share", hint: "Your share of the words spoken", format: (v) => `${Math.round(v * 100)}%` },
  { key: "avg_turn_words", label: "Average turn", hint: "Words per uninterrupted turn", format: (v) => `${Math.round(v)} words` },
  { key: "longest_turn_words", label: "Longest turn", hint: "Your longest monologue", format: (v) => `${Math.round(v)} words` },
  { key: "questions", label: "Questions asked", hint: "Per meeting", format: (v) => v.toFixed(1).replace(/\.0$/, "") },
  { key: "fillers_per_100_words", label: "Filler words", hint: "um, uh, you know… per 100 words", format: (v) => v.toFixed(1) },
];

/* From your own microphone track, only for meetings whose audio was kept. */
const VOICE_METRICS: Metric[] = [
  { key: "speaking_rate_wpm", label: "Pace", hint: "Words per minute while you speak", format: (v) => `${Math.round(v)} wpm` },
  { key: "pauses_per_minute", label: "Pauses", hint: "Silences over half a second, per minute", format: (v) => `${v.toFixed(1)}/min` },
  { key: "pitch_range_semitones", label: "Pitch variety", hint: "How far your pitch moves, in semitones", format: (v) => `${v.toFixed(1)} st` },
  { key: "loudness_range_db", label: "Loudness range", hint: "How much your volume moves", format: (v) => `${v.toFixed(1)} dB` },
];

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function Sparkline({ meetings, metric, format }: {
  meetings: Meeting[];
  metric: TrendMetric;
  format: (v: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const points = meetings
    .slice(-SPARK_POINTS)
    .flatMap((m) => (m.stats[metric] === null ? [] : [{ m, v: m.stats[metric] as number }]));
  if (points.length < 2) return <div className="coach-spark coach-spark-empty">Needs 2+ meetings</div>;
  const w = 300;
  const h = 48;
  const pad = 5;
  const values = points.map((p) => p.v);
  const top = Math.max(...values);
  const bottom = Math.min(...values);
  // A floor on the vertical span so a 1 wpm wobble is not drawn as a climb.
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length;
  const span = Math.max(top - bottom, Math.abs(mean) * 0.25) || 1;
  const lo = (top + bottom) / 2 - span / 2;
  const x = (i: number) => pad + (i * (w - 2 * pad)) / (points.length - 1);
  const y = (v: number) => h - pad - ((v - lo) / span) * (h - 2 * pad);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const active = hover === null ? null : points[hover];
  return (
    <div className="coach-spark">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Trend over your last ${points.length} meetings`}
        onMouseLeave={() => setHover(null)}>
        <path d={path} className="coach-spark-line" />
        {active && <line x1={x(hover!)} x2={x(hover!)} y1={0} y2={h} className="coach-spark-rule" />}
        {points.map((p, i) => (
          <g key={p.m.name}>
            {i === hover && <circle cx={x(i)} cy={y(p.v)} r={4} className="coach-spark-dot" />}
            <rect x={x(i) - (w / points.length) / 2} y={0} width={w / points.length} height={h}
              fill="transparent" onMouseEnter={() => setHover(i)} />
          </g>
        ))}
      </svg>
      {active && (
        <div className="coach-tip" style={{ left: `${(x(hover!) / w) * 100}%` }}>
          <strong>{format(active.v)}</strong>
          <span>{shortDate(active.m.started)} · {active.m.title}</span>
        </div>
      )}
    </div>
  );
}

function MetricTile({ data, metric }: { data: Dashboard; metric: Metric }) {
  const { recent, earlier } = data.trends[metric.key];
  return (
    <article className="coach-tile">
      <h3>{metric.label}</h3>
      <p className="coach-tile-value">{recent === null ? "—" : metric.format(recent)}</p>
      <p className="coach-tile-sub">
        {recent !== null && earlier !== null
          ? `was ${metric.format(earlier)} in the ${data.window} meetings before`
          : `${metric.hint}, last ${data.window} meetings`}
      </p>
      <Sparkline meetings={data.meetings} metric={metric.key} format={metric.format} />
    </article>
  );
}

export function CoachingDashboard() {
  const openNote = useStore((s) => s.openNote);
  const [state, setState] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    coachingApi
      .dashboard()
      .then((data) => live && setState({ kind: "ready", data }))
      .catch(() => live && setState({ kind: "failed" }));
    return () => {
      live = false;
    };
  }, []);

  const data = state.kind === "ready" ? state.data : null;
  const newestFirst = data ? [...data.meetings].reverse() : [];
  const reviewed = newestFirst.filter((m) => m.focus);
  const hasVoice = newestFirst.some((m) => m.stats.speaking_rate_wpm !== null);

  return (
    <div className="page coach-page">
      <header className="page-head">
        <h1>Coaching</h1>
        <p>
          How you speak in meetings, over time. Speaking stats come from your transcripts, voice
          stats from your own microphone, and focus areas from meetings you reviewed with “Analyze
          full meeting”. Everything stays on this Mac.
        </p>
      </header>
      {state.kind === "loading" && <p className="page-loading">Loading your progress…</p>}
      {state.kind === "failed" && <p className="page-loading">Could not load your progress.</p>}
      {data && data.meetings.length === 0 && (
        <p className="page-loading">No meetings with your own speech yet. Record one and come back.</p>
      )}
      {data && data.meetings.length > 0 && (
        <>
          <section className="coach-tiles" aria-label="Speaking stats">
            {METRICS.map((metric) => <MetricTile key={metric.key} data={data} metric={metric} />)}
          </section>

          <section className="coach-section">
            <h2>Your voice</h2>
            {hasVoice ? (
              <div className="coach-tiles coach-tiles-tight" aria-label="Voice stats">
                {VOICE_METRICS.map((metric) => <MetricTile key={metric.key} data={data} metric={metric} />)}
              </div>
            ) : (
              <p className="settings-intro">
                Voice stats are measured on your own microphone, so they need kept recordings. Turn on
                “Keep meeting recordings” in Settings; meetings recorded from then on show up here.
              </p>
            )}
          </section>

          <section className="coach-section">
            <h2>What you're working on</h2>
            {reviewed.length === 0 ? (
              <p className="settings-intro">
                Open a meeting's Transcript tab and choose “Analyze full meeting” to get a practice focus here.
              </p>
            ) : (
              <ol className="coach-focus">
                {reviewed.map((m) => (
                  <li key={m.name}>
                    <p>{m.focus}</p>
                    {m.patterns.length > 0 && <p className="coach-focus-patterns">{m.patterns.join(" · ")}</p>}
                    <button type="button" className="link" onClick={() => void openNote(m.name, { tab: "transcript" })}>
                      {shortDate(m.started)} · {m.title}
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="coach-section">
            <h2>Meetings</h2>
            <div className="coach-table-wrap">
              <table className="coach-table">
                <thead>
                  <tr>
                    <th scope="col">Meeting</th>
                    <th scope="col">Talk share</th>
                    <th scope="col">Turns</th>
                    <th scope="col">Avg turn</th>
                    <th scope="col">Questions</th>
                    <th scope="col">Fillers /100</th>
                    <th scope="col">Pace</th>
                    <th scope="col">Reviewed</th>
                  </tr>
                </thead>
                <tbody>
                  {newestFirst.map((m) => (
                    <tr key={m.name}>
                      <th scope="row">
                        <button type="button" className="link" onClick={() => void openNote(m.name, { tab: "transcript" })}>
                          {m.title}
                        </button>
                        <small>{shortDate(m.started)}</small>
                      </th>
                      <td>{m.stats.talk_share === null ? "—" : `${Math.round(m.stats.talk_share * 100)}%`}</td>
                      <td>{m.stats.turns}</td>
                      <td>{Math.round(m.stats.avg_turn_words)}</td>
                      <td>{m.stats.questions}</td>
                      <td>{m.stats.fillers_per_100_words.toFixed(1)}</td>
                      <td>{m.stats.speaking_rate_wpm === null ? "—" : `${m.stats.speaking_rate_wpm} wpm`}</td>
                      <td>{m.focus ? "Yes" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
