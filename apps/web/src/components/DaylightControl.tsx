/**
 * The time of day, for a rebuilt flat baked at several: how the light falls
 * in the morning, at noon, in the evening.
 *
 * A drag crossfades between the bakes either side. Let go and it glides to
 * the nearest bake: halfway between two, the light is two half-strength suns
 * on the floor rather than one, which no real afternoon has. The bakes are
 * marked under the track and can be tapped; the track is the sky's colour
 * through the day.
 */

import { useEffect, useRef } from 'react';
import { formatMinutes, mood, nearestKey, skyColour } from '../lib/daylight';

interface Props {
  /** The baked times, minutes after midnight, earliest first. */
  keys: number[];
  value: number;
  onChange: (minutes: number) => void;
}

const THUMB = 22;

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
  const settle = () => glideTo(nearestKey(keys, latest.current));

  const at = (minutes: number) => (minutes - lo) / (hi - lo);
  const track = `linear-gradient(to right, ${keys
    .map((k) => `${skyColour(k)} ${(at(k) * 100).toFixed(1)}%`)
    .join(', ')})`;
  const current = nearestKey(keys, value);

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
        onPointerUp={settle}
        onPointerCancel={settle}
        onKeyDown={(e) => {
          // A key goes from one bake to the next, as the stops below do.
          const forward = e.key === 'ArrowRight' || e.key === 'ArrowUp';
          const back = e.key === 'ArrowLeft' || e.key === 'ArrowDown';
          if (!forward && !back) return;
          e.preventDefault();
          const i = keys.indexOf(current);
          glideTo(keys[Math.max(0, Math.min(keys.length - 1, i + (forward ? 1 : -1)))]);
        }}
      />
      <div className="relative h-4" aria-hidden="true">
        {keys.map((k) => (
          <button
            key={k}
            type="button"
            tabIndex={-1}
            onClick={() => glideTo(k)}
            className={[
              'measure absolute top-0 -translate-x-1/2 px-1 text-[10px] leading-4',
              k === current ? 'font-semibold opacity-90' : 'opacity-55',
            ].join(' ')}
            style={{ left: `calc(${THUMB / 2}px + (100% - ${THUMB}px) * ${at(k)})` }}
          >
            {formatMinutes(k)}
          </button>
        ))}
      </div>
    </div>
  );
}
