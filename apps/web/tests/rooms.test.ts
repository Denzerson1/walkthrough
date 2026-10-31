import { describe, expect, it } from 'vitest';

import type { Room, Vec2 } from '../src/lib/manifest';
import { doorways, nearestOnStretch, roomArea, route, sharedStretch } from '../src/lib/rooms';

const room = (id: string, floorPolygon: Vec2[], areaM2?: number): Room => ({
  id,
  name: id,
  type: 'other',
  waypoint: { position: [0, 1.6, 0], yaw: 0 },
  floorPolygon,
  walls: [],
  openings: [],
  ...(areaM2 ? { areaM2 } : {}),
});

// A living room with a hall below it and a bedroom beside it; a box room
// off on its own.
const living = room('living', [[0, 0], [4, 0], [4, 5], [0, 5]]);
const hall = room('hall', [[1, 0], [1, -2.5], [3, -2.5], [3, 0]]);
const bedroom = room('bedroom', [[0, 5], [0, 0], [-2.5, 0], [-2.5, 5]]);
const away = room('away', [[10, 10], [12, 10], [12, 12], [10, 12]]);

describe('room doorways', () => {
  it('finds the stretch of outline each neighbour shares', () => {
    const found = Object.fromEntries(
      doorways([living, hall, bedroom, away], 'living').map((d) => [d.roomId, d.along]),
    );
    const sorted = (s: [Vec2, Vec2]) => [...s].sort((p, q) => p[0] - q[0] || p[1] - q[1]);
    expect(sorted(found.hall)).toEqual([[1, 0], [3, 0]]);
    expect(sorted(found.bedroom)).toEqual([[0, 0], [0, 5]]);
  });

  it('stands a label on the way in nearest you, clear of its ends', () => {
    const along: [Vec2, Vec2] = [[0, 0], [0, 5]];
    expect(nearestOnStretch(along, [2, 3.2])).toEqual([0, 3.2]);
    expect(nearestOnStretch(along, [2, -4])).toEqual([0, 0.4]);
    expect(nearestOnStretch(along, [2, 9])).toEqual([0, 4.6]);
  });

  it('leaves out the current room and rooms that do not border it', () => {
    const ids = doorways([living, hall, bedroom, away], 'living').map((d) => d.roomId);
    expect(ids).not.toContain('living');
    expect(ids).not.toContain('away');
    expect(doorways([living, hall, bedroom], 'hall').map((d) => d.roomId)).toEqual(['living']);
  });

  it('takes the longest shared stretch, and none for outlines that only touch at a corner', () => {
    expect(sharedStretch([[0, 0], [1, 0], [1, 1], [0, 1]], [[1, 1], [2, 1], [2, 2], [1, 2]])).toBeNull();
  });
});

describe('room area', () => {
  it("is the listing's figure where it gives one, else the outline's", () => {
    expect(roomArea(living)).toBeCloseTo(20);
    expect(roomArea({ ...living, areaM2: 21.5 })).toBe(21.5);
  });
});

describe('the way between rooms', () => {
  it('goes through the rooms between, crossing a door square', () => {
    // A box room off the hall through a 0.8 m door at y = -2.5.
    const box = room('box', [[1.5, -2.5], [2.3, -2.5], [2.3, -4], [1.5, -4]]);
    const points = route([living, hall, bedroom, box], 'box', 'bedroom', [1.9, -3.5], [-1.5, 2.5]);
    expect(points[0]).toEqual([1.9, -3.5]);
    expect(points[points.length - 1]).toEqual([-1.5, 2.5]);
    // Before and after the door, square to it.
    const crossing = points.findIndex((p) => p[1] > -2.5);
    expect(points[crossing - 1][1]).toBeCloseTo(-2.85);
    expect(points[crossing][1]).toBeCloseTo(-2.15);
    expect(points[crossing][0]).toBeCloseTo(points[crossing - 1][0]);
    // Then through the wide openings at their shortest: living's (y = 0), bedroom's (x = 0).
    expect(points.some((p) => Math.abs(p[1]) < 1e-9 && p[0] >= 1.45 && p[0] <= 2.55)).toBe(true);
    expect(points.some((p) => Math.abs(p[0]) < 1e-9)).toBe(true);
  });

  it('takes a wide opening where the way is shortest, clear of its ends', () => {
    // From the hall straight up into the living room: no detour.
    expect(route([living, hall], 'hall', 'living', [2, -2], [2, 2])).toEqual([[2, -2], [2, 0], [2, 2]]);
    expect(route([living, hall], 'hall', 'living', [2.9, -2], [3.9, 2])[1][0]).toBeCloseTo(2.55);
  });

  it('does not count two corners touching as a way in', () => {
    const corner = room('corner', [[4, 4.7], [4.3, 4.7], [4.3, 6], [4, 6]]);
    expect(sharedStretch(living.floorPolygon, corner.floorPolygon)).not.toBeNull();
    expect(doorways([living, corner], 'living')).toEqual([]);
  });

  it('goes straight to a room with no way into it', () => {
    expect(route([living, away], 'living', 'away', [1, 1], [11, 11])).toEqual([[1, 1], [11, 11]]);
  });
});
