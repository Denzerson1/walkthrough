import { describe, expect, it } from 'vitest';
import type { SunSample } from '../src/lib/manifest';
import { sunAt } from '../src/lib/sun';

const path: SunSample[] = [
  { minutes: 1050, direction: [1, 0, 0], light: [4, 3, 2] },
  { minutes: 1060, direction: [0, 1, 0], light: [2, 1, 0] },
  { minutes: 1070, direction: [0, 0, 1], light: [0, 0, 0] },
];

describe('the live sun', () => {
  it('moves between the samples of its path', () => {
    const halfway = sunAt(path, 1055);
    expect(halfway.light).toEqual([3, 2, 1]);
    // Halfway between two directions, and still a direction.
    expect(halfway.direction[0]).toBeCloseTo(Math.SQRT1_2);
    expect(halfway.direction[1]).toBeCloseTo(Math.SQRT1_2);
    expect(Math.hypot(...halfway.direction)).toBeCloseTo(1);
    expect(sunAt(path, 1065).light).toEqual([1, 0.5, 0]);
  });

  it('holds at the ends of its path', () => {
    expect(sunAt(path, 900)).toEqual({ direction: [1, 0, 0], light: [4, 3, 2] });
    expect(sunAt(path, 1300)).toEqual({ direction: [0, 0, 1], light: [0, 0, 0] });
    expect(sunAt([path[0]], 1200).light).toEqual([4, 3, 2]);
  });
});
