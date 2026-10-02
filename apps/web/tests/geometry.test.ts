import { describe, expect, it } from 'vitest';
import {
  angleDelta,
  boxCorners,
  boxesOverlap,
  boxInsidePolygon,
  closestPointOnSegment,
  distancePointToSegment,
  pointInPolygon,
  polygonArea,
  polygonBounds,
  polygonCentroid,
  snapToWall,
  snapYaw,
  wallLength,
  wallYaw,
  type OrientedBox,
} from '../src/lib/geometry';
import type { Vec2 } from '../src/lib/manifest';

const SQUARE: Vec2[] = [
  [0, 0],
  [4, 0],
  [4, 3],
  [0, 3],
];

describe('polygonArea', () => {
  it('is positive for counter-clockwise winding', () => {
    expect(polygonArea(SQUARE)).toBeCloseTo(12);
  });

  it('is negative for clockwise winding', () => {
    expect(polygonArea([...SQUARE].reverse())).toBeCloseTo(-12);
  });

  it('is zero for a degenerate polygon', () => {
    expect(polygonArea([[0, 0], [1, 1], [2, 2]])).toBeCloseTo(0);
  });
});

describe('polygonCentroid', () => {
  it('finds the centre of a rectangle', () => {
    expect(polygonCentroid(SQUARE)).toEqual([2, 1.5]);
  });

  it('falls back to the vertex average when the area is zero', () => {
    const c = polygonCentroid([[0, 0], [2, 2], [4, 4]]);
    expect(c[0]).toBeCloseTo(2);
    expect(c[1]).toBeCloseTo(2);
  });
});

describe('pointInPolygon', () => {
  it('accepts an interior point', () => {
    expect(pointInPolygon([2, 1.5], SQUARE)).toBe(true);
  });

  it('rejects an exterior point', () => {
    expect(pointInPolygon([5, 1.5], SQUARE)).toBe(false);
  });

  it('treats a point on an edge as inside', () => {
    expect(pointInPolygon([2, 0], SQUARE)).toBe(true);
  });

  it('treats a vertex as inside', () => {
    expect(pointInPolygon([0, 0], SQUARE)).toBe(true);
  });

  it('handles an L-shaped room concavity', () => {
    const l: Vec2[] = [
      [0, 0],
      [4, 0],
      [4, 2],
      [2, 2],
      [2, 4],
      [0, 4],
    ];
    expect(pointInPolygon([1, 3], l)).toBe(true);
    // Inside the bounding box but outside the L.
    expect(pointInPolygon([3, 3], l)).toBe(false);
  });
});

describe('distancePointToSegment', () => {
  it('measures perpendicular distance', () => {
    expect(distancePointToSegment([2, 2], [0, 0], [4, 0])).toBeCloseTo(2);
  });

  it('clamps past the segment end', () => {
    expect(distancePointToSegment([6, 0], [0, 0], [4, 0])).toBeCloseTo(2);
  });

  it('handles a zero-length segment', () => {
    expect(distancePointToSegment([3, 4], [0, 0], [0, 0])).toBeCloseTo(5);
  });
});

describe('boxCorners', () => {
  it('returns an unrotated box in order', () => {
    const corners = boxCorners({ center: [0, 0], size: [2, 1], yaw: 0 });
    expect(corners[0][0]).toBeCloseTo(-1);
    expect(corners[0][1]).toBeCloseTo(-0.5);
    expect(corners[2][0]).toBeCloseTo(1);
    expect(corners[2][1]).toBeCloseTo(0.5);
  });

  it('rotates by 90 degrees', () => {
    const corners = boxCorners({ center: [0, 0], size: [2, 1], yaw: 90 });
    // The 2 m extent now runs along Y.
    const xs = corners.map((c) => c[0]);
    const ys = corners.map((c) => c[1]);
    expect(Math.max(...xs) - Math.min(...xs)).toBeCloseTo(1);
    expect(Math.max(...ys) - Math.min(...ys)).toBeCloseTo(2);
  });
});

describe('boxesOverlap', () => {
  const a: OrientedBox = { center: [0, 0], size: [2, 2], yaw: 0 };

  it('detects a clear overlap', () => {
    expect(boxesOverlap(a, { center: [1, 0], size: [2, 2], yaw: 0 })).toBe(true);
  });

  it('reports no overlap when separated', () => {
    expect(boxesOverlap(a, { center: [3, 0], size: [2, 2], yaw: 0 })).toBe(false);
  });

  it('respects a clearance margin', () => {
    const b: OrientedBox = { center: [2.5, 0], size: [2, 2], yaw: 0 };
    expect(boxesOverlap(a, b)).toBe(false);
    // 0.8 m clearance (the brief's walkway rule) makes them conflict.
    expect(boxesOverlap(a, b, 0.8)).toBe(true);
  });

  it('detects overlap only visible when rotation is accounted for', () => {
    const rotated: OrientedBox = { center: [1.9, 0], size: [2, 0.2], yaw: 90 };
    // Axis-aligned bounds would say these are apart; the rotated box is thin.
    expect(boxesOverlap(a, rotated)).toBe(false);
  });
});

describe('boxInsidePolygon', () => {
  it('accepts a box well inside the room', () => {
    expect(boxInsidePolygon({ center: [2, 1.5], size: [1, 1], yaw: 0 }, SQUARE)).toBe(true);
  });

  it('rejects a box poking through a wall', () => {
    expect(boxInsidePolygon({ center: [3.8, 1.5], size: [1, 1], yaw: 0 }, SQUARE)).toBe(false);
  });
});

describe('wall helpers', () => {
  it('computes yaw along +X as zero', () => {
    expect(wallYaw({ start: [0, 0], end: [4, 0] })).toBeCloseTo(0);
  });

  it('computes yaw along +Y as 90 degrees', () => {
    expect(wallYaw({ start: [0, 0], end: [0, 4] })).toBeCloseTo(90);
  });

  it('measures length', () => {
    expect(wallLength({ start: [0, 0], end: [3, 4] })).toBeCloseTo(5);
  });
});

describe('snapToWall', () => {
  const walls = [
    { start: [0, 0] as Vec2, end: [4, 0] as Vec2 },
    { start: [4, 0] as Vec2, end: [4, 3] as Vec2 },
  ];

  it('snaps a nearby box flush to the wall', () => {
    const box: OrientedBox = { center: [2, 0.45], size: [2, 0.9], yaw: 3 };
    const snapped = snapToWall(box, walls, SQUARE);
    expect(snapped).not.toBeNull();
    expect(snapped!.wallIndex).toBe(0);
    // Back edge touches y=0, so the centre sits at half the depth.
    expect(snapped!.position[1]).toBeCloseTo(0.45);
    expect(snapped!.yaw).toBeCloseTo(0);
  });

  it('returns null when no wall is within the threshold', () => {
    const box: OrientedBox = { center: [2, 1.5], size: [1, 1], yaw: 0 };
    expect(snapToWall(box, walls, SQUARE)).toBeNull();
  });

  it('keeps the snapped position inside the room', () => {
    const box: OrientedBox = { center: [3.6, 1.5], size: [1, 0.8], yaw: 0 };
    const snapped = snapToWall(box, walls, SQUARE);
    expect(snapped).not.toBeNull();
    expect(pointInPolygon(snapped!.position, SQUARE)).toBe(true);
  });
});

describe('snapYaw', () => {
  it('snaps to the nearest 15 degrees', () => {
    expect(snapYaw(7)).toBe(0);
    expect(snapYaw(8)).toBe(15);
    expect(snapYaw(97)).toBe(90);
  });

  it('normalises into 0..360', () => {
    expect(snapYaw(-15)).toBe(345);
    expect(snapYaw(375)).toBe(15);
  });
});

describe('angleDelta', () => {
  it('takes the short way round', () => {
    expect(angleDelta(350, 10)).toBeCloseTo(20);
    expect(angleDelta(10, 350)).toBeCloseTo(-20);
  });

  it('returns zero for equal angles', () => {
    expect(angleDelta(90, 90)).toBeCloseTo(0);
  });
});

describe('closestPointOnSegment', () => {
  it('projects onto the segment', () => {
    expect(closestPointOnSegment([2, 5], [0, 0], [4, 0])).toEqual([2, 0]);
  });
});

describe('polygonBounds', () => {
  it('returns min and max corners', () => {
    expect(polygonBounds(SQUARE)).toEqual({ min: [0, 0], max: [4, 3] });
  });
});

/**
 * Parity with facing_yaw in layout.py.
 *
 * snapToWall used to return wallYaw — the direction the wall RUNS in — so a
 * snapped item faced along the wall, and in a clockwise-wound room it faced
 * into it. The Python twin was fixed; this copy was not, and nothing caught
 * it because no production code calls snapToWall yet.
 */
describe('snapToWall facing', () => {
  function front(yaw: number): Vec2 {
    const r = (yaw * Math.PI) / 180;
    return [-Math.sin(r), Math.cos(r)];
  }

  const CCW: Vec2[] = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ];
  const CW: Vec2[] = [
    [0, 0],
    [0, 3],
    [4, 3],
    [4, 0],
  ];

  it('faces into the room from the bottom wall, counter-clockwise winding', () => {
    const walls = [{ start: [0, 0] as Vec2, end: [4, 0] as Vec2 }];
    const snapped = snapToWall({ center: [2, 0.45], size: [2, 0.9], yaw: 0 }, walls, CCW);
    expect(snapped).not.toBeNull();
    const [fx, fy] = front(snapped!.yaw);
    // Room interior is +z from this wall.
    expect(fx * 0 + fy * 1).toBeGreaterThan(0.99);
  });

  it('faces into the room from a left wall, clockwise winding', () => {
    const walls = [{ start: [0, 0] as Vec2, end: [0, 3] as Vec2 }];
    const snapped = snapToWall({ center: [0.45, 1.5], size: [2, 0.9], yaw: 0 }, walls, CW);
    expect(snapped).not.toBeNull();
    const [fx, fy] = front(snapped!.yaw);
    // Room interior is +x from this wall.
    expect(fx * 1 + fy * 0).toBeGreaterThan(0.99);
  });

  it('never faces out of the room, whichever way the wall is wound', () => {
    for (const poly of [CCW, CW]) {
      for (let i = 0; i < poly.length; i++) {
        const wall = { start: poly[i], end: poly[(i + 1) % poly.length] };
        const mid: Vec2 = [
          (wall.start[0] + wall.end[0]) / 2,
          (wall.start[1] + wall.end[1]) / 2,
        ];
        const centre: Vec2 = [2, 1.5];
        const toCentre: Vec2 = [centre[0] - mid[0], centre[1] - mid[1]];
        const len = Math.hypot(...toCentre) || 1;
        // Place the box just inside the wall so it snaps to this one.
        const box = {
          center: [mid[0] + (toCentre[0] / len) * 0.45, mid[1] + (toCentre[1] / len) * 0.45] as Vec2,
          size: [1, 0.9] as Vec2,
          yaw: 0,
        };
        const snapped = snapToWall(box, [wall], poly);
        if (!snapped) continue;
        const [fx, fy] = front(snapped.yaw);
        expect(fx * (toCentre[0] / len) + fy * (toCentre[1] / len)).toBeGreaterThan(0);
      }
    }
  });
});
