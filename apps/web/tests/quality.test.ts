import { describe, expect, it } from 'vitest';

import { Quality } from '../src/lib/quality';

/** Feed frames of `ms` for `seconds`, from `start`; the time reached. */
function run(q: Quality, ms: number, seconds: number, start = 0): number {
  let now = start;
  for (; now < start + seconds * 1000; now += ms) q.frame(ms, now);
  return now;
}

/** Frames alternating 16.7 and 33.3 ms: about 40 fps on a 60 Hz display. */
function judder(q: Quality, seconds: number, start = 0): number {
  let now = start;
  for (let i = 0; now < start + seconds * 1000; i++) {
    const ms = i % 2 ? 33.3 : 16.7;
    now += ms;
    q.frame(ms, now);
  }
  return now;
}

describe('adaptive quality', () => {
  it('caps the pixel ratio at 1.5 on a phone and 2 on a desktop', () => {
    expect(new Quality(3, 1.5).pixelRatio).toBe(1.5);
    expect(new Quality(3, 2).pixelRatio).toBe(2);
    expect(new Quality(1, 2).pixelRatio).toBe(1);
  });

  it('keeps a steady 60 fps as it is', () => {
    const q = new Quality(2, 2);
    run(q, 16.7, 20);
    expect(q.pixelRatio).toBe(2);
  });

  it('steps down from 40 fps, though single frames jump between 60 and 30', () => {
    const q = new Quality(2, 2);
    judder(q, 0.9);
    expect(q.pixelRatio).toBe(2);
    judder(q, 0.5, 900);
    expect(q.pixelRatio).toBeLessThan(2);
  });

  it('goes straight to the ratio that fits, not a quarter at a time', () => {
    const q = new Quality(2, 2);
    // Four times over budget: half the ratio draws a quarter of the pixels.
    run(q, 80, 2);
    expect(q.pixelRatio).toBe(1);
  });

  it('then lowers splat detail once the pixel ratio is down to 1', () => {
    const q = new Quality(1.5, 1.5);
    let now = 0;
    for (let i = 0; i < 3; i++) now = run(q, 60, 1.5, now);
    expect(q.pixelRatio).toBe(1);
    expect(q.splatDetail).toBeLessThan(1);
  });

  it('steps back up after 8 s of room to spare, but not to a ratio that was too slow', () => {
    const q = new Quality(2, 2);
    let now = run(q, 26, 1.5);
    expect(q.pixelRatio).toBe(1.75);
    now = run(q, 16.7, 7, now);
    expect(q.pixelRatio).toBe(1.75);
    run(q, 16.7, 20, now);
    expect(q.pixelRatio).toBe(1.75);
  });

  it('does not step down on a single slow frame', () => {
    const q = new Quality(2, 2);
    let now = run(q, 16.7, 1);
    q.frame(200, now);
    now = run(q, 16.7, 2, now + 200);
    expect(q.pixelRatio).toBe(2);
  });
});
