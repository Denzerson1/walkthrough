/**
 * The time of day, for a rebuilt flat baked at several: how the light falls
 * in the morning, at noon, in the evening.
 *
 * The sun is drawn live (sun.ts), so any time on the track is a real one:
 * dragging walks its patch across the room, and the hours marked under the
 * track glide there. The track is the sky's colour through the day.
 */

import { useEffect, useRef } from 'react';
import { formatMinutes, mood, skyColour } from '../lib/daylight';

interface Props {
  /** The baked times, minutes after midnight, earliest first: the track's span. */
  keys: number[];
  value: number;
  onChange: (minutes: number) => void;
}

const THUMB = 22;
/** Hours marked under the track. */
const MARKS = [8, 12, 16, 20].map((h) => h * 60);
/** How far an arrow key moves the time, minutes. */
const NUDGE = 15;

export function DaylightControl({ keys, value, onChange }: Props) {
  const [lo, hi] = [keys[0], keys[keys.length - 1]];
  // The latest value, for a glide that starts from wherever the last one left it.
  const latest = useRef(value);
  useEffect(() => {
    latest.current = value;
  }, [value]);
  const glide = useRef(0);
  useEffect(() => () => cancelAnimationFrame(glide.current), []);

  /** Ease to `target`, slower the further it is, as time passing would. */
  const glideTo = (target: number) => {
    cancelAnimationFrame(glide.current);
    const from = latest.current;
    const start = performance.now();
    const duration = Math.min(1600, 250 + Math.abs(target - from) * 2.5);
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      onChange(from + (target - from) * (1 - (1 - t) ** 3));
      if (t < 1) glide.current = requestAnimationFrame(step);
    };
    glide.current = requestAnimationFrame(step);
  };

  const at = (minutes: number) => (minutes - lo) / (hi - lo);
  const stops = Array.from({ length: 9 }, (_, k) => lo + ((hi - lo) * k) / 8);
  const track = `linear-gradient(to right, ${stops
    .map((m) => `${skyColour(m)} ${(at(m) * 100).toFixed(1)}%`)
    .join(', ')})`;
  const marks = MARKS.filter((m) => m >= lo && m <= hi);

  return (
    <div>
      <label className="flex items-baseline justify-between text-[11px]" htmlFor="daylight">
        <span>
          <span className="opacity-65">Daylight</span>
          <span className="ml-1.5 font-medium">{mood(value)}</span>
        </span>
        <span className="font-medium tabular-nums" data-testid="daylight-time">
          {formatMinutes(value)}
        </span>
      </label>
      <input
        id="daylight"
        type="range"
        className="daylight-range mt-1"
        style={{ ['--track' as string]: track }}
        min={lo}
        max={hi}
        step={5}
        value={value}
        aria-valuetext={`${mood(value)}, ${formatMinutes(value)}`}
        onChange={(e) => {
          cancelAnimationFrame(glide.current);
          onChange(Number(e.target.value));
        }}
        onKeyDown={(e) => {
          const forward = e.key === 'ArrowRight' || e.key === 'ArrowUp';
          const back = e.key === 'ArrowLeft' || e.key === 'ArrowDown';
          if (!forward && !back) return;
          e.preventDefault();
          glideTo(Math.max(lo, Math.min(hi, latest.current + (forward ? NUDGE : -NUDGE))));
        }}
      />
      <div className="relative h-4" aria-hidden="true">
        {marks.map((m) => (
          <button
            key={m}
            type="button"
            tabIndex={-1}
            onClick={() => glideTo(m)}
            className={[
              'measure absolute top-0 -translate-x-1/2 px-1 text-[10px] leading-4',
              Math.abs(m - value) < 30 ? 'font-semibold opacity-90' : 'opacity-55',
            ].join(' ')}
            style={{ left: `calc(${THUMB / 2}px + (100% - ${THUMB}px) * ${at(m)})` }}
          >
            {formatMinutes(m)}
          </button>
        ))}
      </div>
    </div>
  );
}
