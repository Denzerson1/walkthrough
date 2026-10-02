import { describe, expect, it } from 'vitest';
import {
  BODY_RADIUS,
  canStand,
  distanceAlongWall,
  inDoorway,
  moveWithCollision,
  roomAt,
  type WalkableRoom,
} from '../src/lib/geometry';
import type { Vec2 } from '../src/lib/manifest';

/**
 * Two rooms sharing the wall at x = 4, with a 0.9 m door centred on it.
 *
 *   living: (0,0)-(4,3)      bedroom: (4,0)-(7,3)
 *   door:   x = 4, z = 1.5 +/- 0.45
 */
const living: WalkableRoom = {
  floorPolygon: [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ],
  walls: [
    { start: [0, 0], end: [4, 0] },
    { start: [4, 0], end: [4, 3] },
    { start: [4, 3], end: [0, 3] },
    { start: [0, 3], end: [0, 0] },
  ],
  openings: [{ type: 'door', wallIndex: 1, offset: 1.5, width: 0.9 }],
};

const bedroom: WalkableRoom = {
  floorPolygon: [
    [4, 0],
    [7, 0],
    [7, 3],
    [4, 3],
  ],
  walls: [
    { start: [4, 0], end: [7, 0] },
    { start: [7, 0], end: [7, 3] },
    { start: [7, 3], end: [4, 3] },
    { start: [4, 3], end: [4, 0] },
  ],
  // The same doorway seen from the other side: wall 3 runs (4,3)->(4,0), so
  // the offset is measured from z = 3 downwards.
  openings: [{ type: 'door', wallIndex: 3, offset: 1.5, width: 0.9 }],
};

const flat = [living, bedroom];

describe('distanceAlongWall', () => {
  it('measures from the wall start', () => {
    expect(distanceAlongWall([2, 0], living.walls[0])).toBeCloseTo(2);
    expect(distanceAlongWall([4, 1.5], living.walls[1])).toBeCloseTo(1.5);
  });

  it('clamps to the wall rather than running past its end', () => {
    expect(distanceAlongWall([9, 0], living.walls[0])).toBeCloseTo(4);
    expect(distanceAlongWall([-3, 0], living.walls[0])).toBeCloseTo(0);
  });

  it('is zero for a degenerate wall', () => {
    expect(distanceAlongWall([1, 1], { start: [2, 2], end: [2, 2] })).toBe(0);
  });
});

describe('inDoorway', () => {
  it('accepts a point in the door span', () => {
    expect(inDoorway([4, 1.5], living, 1)).toBe(true);
    expect(inDoorway([4, 1.9], living, 1)).toBe(true);
  });

  it('rejects a point past the door jamb', () => {
    expect(inDoorway([4, 2.1], living, 1)).toBe(false);
  });

  it('rejects a wall that has no opening', () => {
    expect(inDoorway([2, 0], living, 0)).toBe(false);
  });

  it('rejects an out-of-range wall index', () => {
    expect(inDoorway([4, 1.5], living, 9)).toBe(false);
  });

  it('does not treat a window as walkable', () => {
    const withWindow: WalkableRoom = {
      ...living,
      openings: [{ type: 'window', wallIndex: 0, offset: 2, width: 1.2 }],
    };
    expect(inDoorway([2, 0], withWindow, 0)).toBe(false);
  });
});

describe('canStand', () => {
  it('accepts the middle of a room', () => {
    expect(canStand([2, 1.5], flat)).toBe(true);
  });

  it('rejects a point outside every room', () => {
    expect(canStand([9, 9], flat)).toBe(false);
  });

  it('rejects standing inside the body radius of a solid wall', () => {
    expect(canStand([0.05, 1.5], flat)).toBe(false);
    expect(canStand([2, 0.05], flat)).toBe(false);
  });

  it('accepts a point just clear of a wall', () => {
    expect(canStand([BODY_RADIUS + 0.01, 1.5], flat)).toBe(true);
  });

  it('accepts the doorway, which is on a wall', () => {
    expect(canStand([4, 1.5], flat)).toBe(true);
  });

  it('rejects the shared wall away from the doorway', () => {
    expect(canStand([4, 0.5], flat)).toBe(false);
  });

  it('ignores a degenerate polygon rather than throwing', () => {
    const degenerate: WalkableRoom = { floorPolygon: [[0, 0]], walls: [], openings: [] };
    expect(canStand([0, 0], [degenerate])).toBe(false);
  });
});

describe('moveWithCollision', () => {
  it('takes the whole step when nothing is in the way', () => {
    const to = moveWithCollision([2, 1.5], [0.3, 0], flat);
    expect(to[0]).toBeCloseTo(2.3);
    expect(to[1]).toBeCloseTo(1.5);
  });

  it('slides along a wall instead of stopping dead', () => {
    // Walking into the south wall at 45°: the z component is refused, the x
    // component survives.
    const from: Vec2 = [2, 0.3];
    const to = moveWithCollision(from, [0.3, -0.3], flat);
    expect(to[0]).toBeCloseTo(2.3);
    expect(to[1]).toBeCloseTo(0.3);
  });

  it('stays put when both axes are blocked', () => {
    const corner: Vec2 = [0.3, 0.3];
    expect(moveWithCollision(corner, [-0.3, -0.3], flat)).toEqual(corner);
  });

  it('walks from one room into the next through the doorway', () => {
    let p: Vec2 = [3.5, 1.5];
    for (let i = 0; i < 20; i++) p = moveWithCollision(p, [0.1, 0], flat);
    expect(p[0]).toBeGreaterThan(4.3);
    expect(roomAt(p, flat)).toBe(bedroom);
  });

  it('cannot cross the shared wall away from the doorway', () => {
    let p: Vec2 = [3.5, 0.6];
    for (let i = 0; i < 20; i++) p = moveWithCollision(p, [0.1, 0], flat);
    expect(p[0]).toBeLessThan(4);
  });
});

describe('roomAt', () => {
  it('finds the room containing a point', () => {
    expect(roomAt([1, 1], flat)).toBe(living);
    expect(roomAt([6, 1], flat)).toBe(bedroom);
  });

  it('returns undefined outside every room', () => {
    expect(roomAt([99, 99], flat)).toBeUndefined();
  });
});
