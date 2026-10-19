import { describe, expect, it } from 'vitest';
import { approach, meter, targetExposure } from '../src/lib/exposure';

describe('auto exposure', () => {
  it('opens up in the dark and stops down in the bright, only partly', () => {
    expect(targetExposure(0.33, 0.33)).toBeCloseTo(1);
    const hall = targetExposure(0.1, 0.33, 0.7);
    expect(hall).toBeGreaterThan(1);
    // Partial: the darker view still ends up darker than the reference.
    expect(0.1 * hall).toBeLessThan(0.33);
    expect(targetExposure(0.9, 0.33)).toBeLessThan(1);
    expect(targetExposure(0, 0.33)).toBe(4); // clamped, as a camera's range is
  });

  it('eases towards the goal without overshooting', () => {
    const once = approach(1, 2, 0.1);
    expect(once).toBeGreaterThan(1);
    expect(once).toBeLessThan(2);
    expect(approach(1, 2, 100)).toBeCloseTo(2);
  });

  it('meters the log-average luminance of what is in view', () => {
    const grey = new Uint8Array(4 * 4 * 4).fill(128);
    expect(meter(grey, 4, 4)).toBeCloseTo(128 / 255 + 1e-3, 2);
    // Half black, half white: a geometric mean sits far below the arithmetic one.
    const split = new Uint8Array(4 * 4 * 4).map((_, i) => (Math.floor(i / 4) % 4 < 2 ? 0 : 255));
    expect(meter(split, 4, 4)).toBeLessThan(0.1);
  });
});
