import { describe, expect, it } from 'vitest';

import { Quality } from '../src/lib/quality';

/** Feed frames of `ms` for `seconds`, from `start`; the time reached. */
function run(q: Quality, ms: number, seconds: number, start = 0): number {
  let now = start;
  for (; now < start + seconds * 1000; now += ms) q.frame(ms, now);
  return now;
}

describe('adaptive quality', () => {
  it('caps the pixel ratio at 1.5 on a phone and 2 on a desktop', () => {
    expect(new Quality(3, 1.5).pixelRatio).toBe(1.5);
    expect(new Quality(3, 2).pixelRatio).toBe(2);
    expect(new Quality(1, 2).pixelRatio).toBe(1);
  });

  it('steps the pixel ratio down after 2 s of frames over 33 ms', () => {
    const q = new Quality(3, 1.5);
    run(q, 40, 1.9);
    expect(q.pixelRatio).toBe(1.5);
    run(q, 40, 0.2, 1900);
    expect(q.pixelRatio).toBe(1.25);
  });

  it('then lowers splat detail once the pixel ratio is down to 1', () => {
    const q = new Quality(1.5, 1.5);
    let now = 0;
    for (let i = 0; i < 4; i++) now = run(q, 50, 2.1, now);
    expect(q.pixelRatio).toBe(1);
    expect(q.splatDetail).toBeLessThan(1);
  });

  it('raises it again only after 10 s of quick frames', () => {
    const q = new Quality(2, 2);
    let now = run(q, 40, 2.1);
    expect(q.pixelRatio).toBe(1.75);
    now = run(q, 16, 9, now);
    expect(q.pixelRatio).toBe(1.75);
    run(q, 16, 1.2, now);
    expect(q.pixelRatio).toBe(2);
  });

  it('does not count a slow spell broken by a quick frame', () => {
    const q = new Quality(2, 2);
    let now = run(q, 40, 1.5);
    q.frame(16, now);
    now = run(q, 40, 1.5, now + 16);
    expect(q.pixelRatio).toBe(2);
  });
});
