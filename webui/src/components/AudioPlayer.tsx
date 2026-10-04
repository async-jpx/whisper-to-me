/* The kept-recording player. usePlayer owns the one <audio> element and its
   clock; <AudioPlayer> draws it like the Hush mark: rounded vertical bars,
   played part in accent, the rest muted. */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { AudioPeaks } from "../api/types";
import { formatClock, spokenClock } from "../lib/time";
import { Icon } from "./Icons";

const RATES = [1, 1.5, 2] as const;
type Rate = (typeof RATES)[number];

export interface Player {
  audioRef: React.RefObject<HTMLAudioElement>;
  playing: boolean;
  time: number;
  duration: number;
  rate: Rate;
  seek(t: number): void;
  play(): void;
  toggle(): void;
  playFrom(t: number): void;
  skip(delta: number): void;
  cycleRate(): void;
}

/* `knownDuration` (from the peaks sidecar) stands in until the file's own
   metadata loads. The element uses preload="none": fetching the recording
   marks it played for the 30-day retention, so only a real play may. A seek
   before then is held and applied once metadata arrives. */
export function usePlayer(knownDuration: number): Player {
  const audioRef = useRef<HTMLAudioElement>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [mediaDuration, setMediaDuration] = useState<number | null>(null);
  const [rate, setRate] = useState<Rate>(1);
  const duration = mediaDuration ?? knownDuration;

  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onMeta = () => {
      if (Number.isFinite(a.duration)) setMediaDuration(a.duration);
      if (pendingSeekRef.current !== null) {
        a.currentTime = pendingSeekRef.current;
        pendingSeekRef.current = null;
      }
    };
    const onTime = () => {
      if (pendingSeekRef.current === null) setTime(a.currentTime);
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("seeked", onTime);
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onPause);
    return () => {
      a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("seeked", onTime);
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onPause);
    };
  }, []);

  // timeupdate fires ~4×/s; follow the playhead per frame while playing.
  useEffect(() => {
    if (!playing) return;
    let frame = requestAnimationFrame(function tick() {
      const a = audioRef.current;
      if (a) setTime(a.currentTime);
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  const seek = useCallback(
    (t: number) => {
      const a = audioRef.current;
      const clamped = Math.min(Math.max(0, t), duration || Infinity);
      setTime(clamped);
      if (!a) return;
      if (a.readyState >= HTMLMediaElement.HAVE_METADATA) {
        a.currentTime = clamped;
      } else {
        pendingSeekRef.current = clamped;
      }
    },
    [duration],
  );

  const play = useCallback(() => {
    audioRef.current?.play().catch(() => {
      /* interrupted by a pause or a source change; the events keep state honest */
    });
  }, []);

  const toggle = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) play();
    else a.pause();
  }, [play]);

  const playFrom = useCallback(
    (t: number) => {
      seek(t);
      play();
    },
    [seek, play],
  );

  const skip = useCallback(
    (delta: number) => {
      const a = audioRef.current;
      const now =
        pendingSeekRef.current ??
        (a && a.readyState >= HTMLMediaElement.HAVE_METADATA ? a.currentTime : time);
      seek(now + delta);
    },
    [seek, time],
  );

  const cycleRate = useCallback(() => {
    setRate((r) => {
      const next = RATES[(RATES.indexOf(r) + 1) % RATES.length] ?? 1;
      if (audioRef.current) audioRef.current.playbackRate = next;
      return next;
    });
  }, []);

  return { audioRef, playing, time, duration, rate, seek, play, toggle, playFrom, skip, cycleRate };
}

const BAR_W = 3;
const BAR_GAP = 2;
const WAVE_H = 44;
const MIN_BAR = 0.08;

/* Max-pool (or stretch) the peaks to exactly `count` bars. */
function resample(peaks: number[], count: number): number[] {
  if (count <= 0) return [];
  if (peaks.length === 0) return Array.from({ length: count }, () => 0.3);
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    const from = Math.floor((i * peaks.length) / count);
    const to = Math.max(from + 1, Math.floor(((i + 1) * peaks.length) / count));
    let max = 0;
    for (let j = from; j < to; j++) max = Math.max(max, peaks[j] ?? 0);
    out.push(max);
  }
  return out;
}

function Waveform({
  peaks,
  player,
}: {
  peaks: number[];
  player: Player;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const clipId = useId();
  const [width, setWidth] = useState(0);
  const { time, duration, seek, toggle, skip } = player;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const count = Math.max(0, Math.floor((width + BAR_GAP) / (BAR_W + BAR_GAP)));
  const bars = useMemo(() => {
    const levels = resample(peaks, count);
    return levels.map((level, i) => {
      const h = Math.max(MIN_BAR, Math.min(1, level)) * WAVE_H;
      return (
        <rect
          key={i}
          x={i * (BAR_W + BAR_GAP)}
          y={(WAVE_H - h) / 2}
          width={BAR_W}
          height={h}
          rx={BAR_W / 2}
        />
      );
    });
  }, [peaks, count]);

  const fraction = duration > 0 ? Math.min(1, time / duration) : 0;
  const drawnWidth = Math.max(0, count * (BAR_W + BAR_GAP) - BAR_GAP);

  const seekToPointer = (evt: React.PointerEvent) => {
    const rect = evt.currentTarget.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width));
    seek(f * duration);
  };

  const onKeyDown = (evt: React.KeyboardEvent) => {
    const step: Record<string, () => void> = {
      ArrowLeft: () => skip(-5),
      ArrowRight: () => skip(5),
      ArrowDown: () => skip(-5),
      ArrowUp: () => skip(5),
      PageDown: () => skip(-30),
      PageUp: () => skip(30),
      Home: () => seek(0),
      End: () => seek(duration),
      " ": toggle,
    };
    const action = step[evt.key];
    if (!action) return;
    evt.preventDefault();
    action();
  };

  return (
    <div
      ref={ref}
      className="wave"
      role="slider"
      tabIndex={0}
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      aria-valuenow={Math.round(time)}
      aria-valuetext={`${spokenClock(time)} of ${spokenClock(duration)}`}
      onPointerDown={(evt) => {
        evt.currentTarget.setPointerCapture(evt.pointerId);
        seekToPointer(evt);
      }}
      onPointerMove={(evt) => {
        if (evt.currentTarget.hasPointerCapture(evt.pointerId)) seekToPointer(evt);
      }}
      onKeyDown={onKeyDown}
    >
      <svg width={drawnWidth} height={WAVE_H} aria-hidden="true">
        <defs>
          <clipPath id={clipId}>
            <rect x={0} y={0} width={fraction * drawnWidth} height={WAVE_H} />
          </clipPath>
        </defs>
        <g className="wave-rest">{bars}</g>
        <g className="wave-played" clipPath={`url(#${CSS.escape(clipId)})`}>
          {bars}
        </g>
      </svg>
    </div>
  );
}

export function AudioPlayer({
  src,
  peaks,
  player,
  following,
  onFollow,
}: {
  src: string;
  peaks: AudioPeaks | null;
  player: Player;
  /* False once the user scrolled away from the playing line. */
  following: boolean;
  onFollow(): void;
}) {
  const { audioRef, playing, time, duration, rate, toggle, skip, cycleRate } = player;
  return (
    <div className="player" role="group" aria-label="Recording">
      <audio ref={audioRef} src={src} preload="none" />
      <button
        className="player-play"
        onClick={toggle}
        aria-label={playing ? "Pause" : "Play"}
      >
        <Icon name={playing ? "pause" : "play"} />
      </button>
      <div className="player-main">
        <Waveform peaks={peaks?.peaks ?? []} player={player} />
        <div className="player-row">
          <span className="player-time">
            <span>{formatClock(time)}</span>
            <span className="player-total"> / {formatClock(duration)}</span>
          </span>
          {playing && !following && (
            <button className="player-follow" onClick={onFollow}>
              <Icon name="arrowDown" />
              Current line
            </button>
          )}
          <span className="player-tools">
            <button className="player-tool" onClick={() => skip(-10)} aria-label="Back 10 seconds">
              <Icon name="back10" />
            </button>
            <button className="player-tool" onClick={() => skip(10)} aria-label="Forward 10 seconds">
              <Icon name="fwd10" />
            </button>
            <button
              className="player-tool player-rate"
              onClick={cycleRate}
              aria-label={`Playback speed ${rate}×`}
            >
              {rate}×
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
