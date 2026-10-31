import { describe, expect, it } from 'vitest';

import { Path, easeInOut, glidePose, headingYaw, walkDuration } from '../src/lib/camera';
import { angleDelta } from '../src/lib/geometry';
import type { Vec2 } from '../src/lib/manifest';

const near = (a: Vec2, b: Vec2, eps = 1e-6) => Math.hypot(a[0] - b[0], a[1] - b[1]) < eps;

describe('glides', () => {
  it('ease in and out without a jolt, at most 1.6 times the average speed', () => {
    expect(easeInOut(0)).toBe(0);
    expect(easeInOut(1)).toBe(1);
    const steps = 1000;
    let top = 0;
    for (let i = 0; i < steps; i++) top = Math.max(top, (easeInOut((i + 1) / steps) - easeInOut(i / steps)) * steps);
    expect(top).toBeLessThan(1.6);
    expect(easeInOut(0.001) * 1000).toBeLessThan(0.01);
  });

  it('looks along the way it heads: yaw 0 down -z, 90 down -x', () => {
    expect(headingYaw([0, -1])).toBeCloseTo(0);
    expect(headingYaw([-1, 0])).toBeCloseTo(90);
    expect(Math.abs(headingYaw([0, 1]))).toBeCloseTo(180);
  });

  it('passes through every point of a path, at an even speed', () => {
    const points: Vec2[] = [[0, 0], [2, 0], [2, 2], [4, 2]];
    const path = new Path(points);
    expect(near(path.at(0).point, [0, 0])).toBe(true);
    expect(near(path.at(1).point, [4, 2])).toBe(true);
    // The corners lie on it.
    for (const p of points) {
      let best = Infinity;
      for (let i = 0; i <= 400; i++) {
        const q = path.at(i / 400).point;
        best = Math.min(best, Math.hypot(q[0] - p[0], q[1] - p[1]));
      }
      expect(best).toBeLessThan(0.02);
    }
    // Even speed: equal fractions, equal ground.
    const step = (f: number) => {
      const [a, b] = [path.at(f).point, path.at(f + 0.01).point];
      return Math.hypot(b[0] - a[0], b[1] - a[1]);
    };
    expect(step(0.2)).toBeCloseTo(step(0.7), 2);
    expect(path.length).toBeGreaterThan(6);
  });

  it('turns no corner of a straight path', () => {
    const path = new Path([[0, 0], [1, 0], [3, 0]]);
    for (let i = 0; i <= 20; i++) expect(Math.abs(path.at(i / 20).point[1])).toBeLessThan(1e-9);
  });

  const walk = (fromYaw: number, toYaw: number) => ({
    from: { position: [0, 1.6, 0] as [number, number, number], yaw: fromYaw, pitch: -20 },
    to: { position: [-2, 1.6, -4] as [number, number, number], yaw: toYaw, pitch: 0 },
    startedAt: 0,
    durationMs: 1000,
    path: new Path([[0, 0], [0, -2], [-2, -4]]),
  });
  const wrap = (yaw: number) => ((yaw % 360) + 360) % 360;

  it('starts and ends a walk between rooms at its poses', () => {
    const g = walk(10, 90);
    const start = glidePose(g, 0);
    expect(start.position).toEqual([0, 1.6, 0]);
    expect(wrap(start.yaw)).toBeCloseTo(10);
    expect(start.pitch).toBeCloseTo(-20);
    const end = glidePose(g, 1);
    expect(end.position[0]).toBeCloseTo(-2);
    expect(end.position[2]).toBeCloseTo(-4);
    expect(wrap(end.yaw)).toBeCloseTo(90);
    expect(end.pitch).toBe(0);
  });

  it('looks ahead on a long walk, turning smoothly', () => {
    const g = walk(10, 90);
    // Most of the way down the corridor (-z) it looks along it, not at the end view yet.
    expect(Math.abs(angleDelta(0, glidePose(g, 0.35).yaw))).toBeLessThan(20);
    let previous = glidePose(g, 0).yaw;
    for (let i = 1; i <= 200; i++) {
      const yaw = glidePose(g, i / 200).yaw;
      expect(Math.abs(yaw - previous)).toBeLessThan(3);
      previous = yaw;
    }
  });

  it('turns straight from view to view when looking ahead would spin right round', () => {
    // Facing back the way it will walk, ending facing back again.
    const g = walk(180, 200);
    for (let i = 0; i <= 20; i++) {
      const yaw = glidePose(g, i / 20).yaw;
      expect(Math.abs(angleDelta(190, yaw))).toBeLessThanOrEqual(10.001);
    }
  });

  it('takes longer the further it goes, within bounds', () => {
    expect(walkDuration(0.5)).toBe(1100);
    expect(walkDuration(4)).toBeGreaterThan(walkDuration(2));
    expect(walkDuration(50)).toBe(2600);
  });
});
