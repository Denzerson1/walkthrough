import { describe, expect, it } from 'vitest';

import type { Room, Vec2 } from '../src/lib/manifest';
import { doorways, nearestOnStretch, roomArea, sharedStretch } from '../src/lib/rooms';

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
