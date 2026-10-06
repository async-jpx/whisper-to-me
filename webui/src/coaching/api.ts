/* Wire types + calls for the daemon's coaching router (coaching/api.py). */

import { getJson, note, sendJson } from "../api/client";

export interface CommunicationAnalysis {
  line_index: number;
  what_worked: string;
  improvement: string;
  try_saying: string;
  delivery: string;
  audio_metrics: {
    source: AudioSource;
    excerpt_seconds: number;
    approx_words_per_minute: number;
    pauses_over_0_4s: number;
    longest_pause_seconds: number;
    volume_variation: string;
  } | null;
}

/* "your microphone" = the user's own echo-cancelled track (or a
   single-source recording); "mixed recording" includes other voices. */
export type AudioSource = "your microphone" | "mixed recording";

export interface VoiceMetrics {
  speaking_seconds: number;
  speaking_rate_wpm: number;
  pauses_per_minute: number;
  loudness_median_dbfs: number;
  loudness_range_db: number;
  pitch_median_hz: number | null;
  pitch_range_semitones: number | null;
}

export type MeetingAudio =
  | ({ source: "your microphone"; interpretation_limit: string } & VoiceMetrics)
  | {
      source: "mixed recording";
      duration_seconds: number;
      approx_speaking_rate_wpm: number | null;
      longer_low_energy_gaps: number;
      energy_variation: string;
      interpretation_limit: string;
    };

export interface MeetingCommunicationAnalysis {
  overall_read: string;
  strengths: string[];
  patterns: Array<{
    pattern: string;
    evidence: number[];
    impact: string;
    change: string;
  }>;
  rewrites: Array<{
    line_index: number;
    original: string;
    revision: string;
    reason: string;
  }>;
  next_time: { practice: string; steps: string[]; structure: string };
  /* Reviews saved before voice coaching carry no `source`, and render as mixed. */
  audio_metrics: MeetingAudio | null;
}

export interface SavedReview {
  analyzed_at: string;
  review: MeetingCommunicationAnalysis;
}

export interface MeetingStats {
  your_words: number;
  /* null for single-source notes, which have no other speaker to compare against. */
  talk_share: number | null;
  turns: number;
  avg_turn_words: number;
  longest_turn_words: number;
  questions: number;
  fillers_per_100_words: number;
  /* Voice stats are null unless your own audio was kept for this meeting. */
  speaking_rate_wpm: number | null;
  pauses_per_minute: number | null;
  pitch_range_semitones: number | null;
  loudness_range_db: number | null;
}

export type TrendMetric =
  | "talk_share"
  | "avg_turn_words"
  | "longest_turn_words"
  | "questions"
  | "fillers_per_100_words"
  | "speaking_rate_wpm"
  | "pauses_per_minute"
  | "pitch_range_semitones"
  | "loudness_range_db";

export interface Dashboard {
  /* Oldest first. */
  meetings: Array<{
    name: string;
    title: string;
    started: string;
    stats: MeetingStats;
    focus: string | null;
    patterns: string[];
  }>;
  trends: Record<TrendMetric, { recent: number | null; earlier: number | null }>;
  window: number;
}

export const coachingApi = {
  analyzeLine: (name: string, lineIndex: number) =>
    sendJson<CommunicationAnalysis>("POST", `${note(name)}/analyze`, { line_index: lineIndex }),
  analyzeMeeting: (name: string) => sendJson<SavedReview>("POST", `${note(name)}/analyze/meeting`),
  savedReview: (name: string) => getJson<SavedReview>(`${note(name)}/analyze/meeting`),
  dashboard: () => getJson<Dashboard>("/api/coaching/dashboard"),
};
