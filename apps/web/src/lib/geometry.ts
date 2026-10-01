/**
 * 2D geometry on the floor plane (y = 0). All units are metres, all angles
 * are degrees unless a name says otherwise.
 *
 * Used by furniture placement (M6), the auto-layout solver (M7) and the
 * editor's floor-polygon tools (M2). Kept dependency-free so it can be
 * unit-tested directly.
 */

import type { Vec2 } from './manifest';

export const DEG = Math.PI / 180;

export function toRad(deg: number): number {
  return deg * DEG;
}

/** Signed area; positive when the polygon winds counter-clockwise. */
export function polygonArea(poly: Vec2[]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % poly.length];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

export function polygonCentroid(poly: Vec2[]): Vec2 {
  const a = polygonArea(poly);
  if (Math.abs(a) < 1e-9) {
    // Degenerate polygon: fall back to the average of the vertices.
    const sum = poly.reduce<Vec2>((acc, p) => [acc[0] + p[0], acc[1] + p[1]], [0, 0]);
    return [sum[0] / poly.length, sum[1] / poly.length];
  }
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % poly.length];
    const cross = x1 * y2 - x2 * y1;
    cx += (x1 + x2) * cross;
    cy += (y1 + y2) * cross;
  }
  return [cx / (6 * a), cy / (6 * a)];
}

/** Ray casting. Points exactly on an edge count as inside. */
export function pointInPolygon(p: Vec2, poly: Vec2[]): boolean {
  const [px, py] = p;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    if (distancePointToSegment(p, a, b) < 1e-9) return true;
  }
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    const intersects = yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

export function distancePointToSegment(p: Vec2, a: Vec2, b: Vec2): number {
  const [px, py] = p;
  const [ax, ay] = a;
  const [bx, by] = b;
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-12) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** An axis-aligned box rotated by `yaw` about its centre. */
export interface OrientedBox {
  center: Vec2;
  /** Full width and depth, not half-extents. */
  size: Vec2;
  yaw: number;
}

/** The four corners, counter-clockwise. */
export function boxCorners(box: OrientedBox): Vec2[] {
  const [cx, cy] = box.center;
  const hw = box.size[0] / 2;
  const hd = box.size[1] / 2;
  const r = toRad(box.yaw);
  const cos = Math.cos(r);
  const sin = Math.sin(r);
  const local: Vec2[] = [
    [-hw, -hd],
    [hw, -hd],
    [hw, hd],
    [-hw, hd],
  ];
  return local.map(([x, y]) => [cx + x * cos - y * sin, cy + x * sin + y * cos] as Vec2);
}

/**
 * Separating axis test for two oriented boxes.
 * `clearance` inflates both boxes, so a positive value enforces a gap.
 */
export function boxesOverlap(a: OrientedBox, b: OrientedBox, clearance = 0): boolean {
  const inflate = (box: OrientedBox): OrientedBox => ({
    ...box,
    size: [box.size[0] + clearance, box.size[1] + clearance],
  });
  const ca = boxCorners(inflate(a));
  const cb = boxCorners(inflate(b));
  for (const corners of [ca, cb]) {
    for (let i = 0; i < 4; i++) {
      const [x1, y1] = corners[i];
      const [x2, y2] = corners[(i + 1) % 4];
      // Axis perpendicular to this edge.
      const axis: Vec2 = [-(y2 - y1), x2 - x1];
      const len = Math.hypot(axis[0], axis[1]);
      if (len < 1e-12) continue;
      const nx = axis[0] / len;
      const ny = axis[1] / len;
      const projA = project(ca, nx, ny);
      const projB = project(cb, nx, ny);
      if (projA.max < projB.min - 1e-9 || projB.max < projA.min - 1e-9) return false;
    }
  }
  return true;
}

function project(corners: Vec2[], nx: number, ny: number): { min: number; max: number } {
  let min = Infinity;
  let max = -Infinity;
  for (const [x, y] of corners) {
    const d = x * nx + y * ny;
    if (d < min) min = d;
    if (d > max) max = d;
  }
  return { min, max };
}

/** True when every corner of the box lies inside the polygon. */
export function boxInsidePolygon(box: OrientedBox, poly: Vec2[]): boolean {
  return boxCorners(box).every((c) => pointInPolygon(c, poly));
}

export interface WallSegment {
  start: Vec2;
  end: Vec2;
}

/** Outward-facing normal is not known from the segment alone, so return both. */
export function wallYaw(wall: WallSegment): number {
  const dx = wall.end[0] - wall.start[0];
  const dy = wall.end[1] - wall.start[1];
  return (Math.atan2(dy, dx) * 180) / Math.PI;
}

export function wallLength(wall: WallSegment): number {
  return Math.hypot(wall.end[0] - wall.start[0], wall.end[1] - wall.start[1]);
}

export interface SnapResult {
  position: Vec2;
  yaw: number;
  wallIndex: number;
  distance: number;
}

/**
 * Snap a box to the nearest wall within `threshold` metres, per the brief's
 * "snap to the nearest wall within 10 cm". The box is rotated to sit flush
 * and pushed out so its back edge touches the wall.
 */
export function snapToWall(
  box: OrientedBox,
  walls: WallSegment[],
  polygon: Vec2[],
  threshold = 0.1,
): SnapResult | null {
  let best: SnapResult | null = null;
  for (let i = 0; i < walls.length; i++) {
    const wall = walls[i];
    const d = distancePointToSegment(box.center, wall.start, wall.end);
    const halfDepth = box.size[1] / 2;
    // Distance from the box's back edge to the wall.
    const gap = Math.abs(d - halfDepth);
    if (gap > threshold) continue;
    if (best && gap >= best.distance) continue;

    const yaw = wallYaw(wall);
    // Push the centre to exactly halfDepth away from the wall, on the side
    // the polygon interior lies.
    const nx = -(wall.end[1] - wall.start[1]);
    const ny = wall.end[0] - wall.start[0];
    const nlen = Math.hypot(nx, ny) || 1;
    const unit: Vec2 = [nx / nlen, ny / nlen];
    const foot = closestPointOnSegment(box.center, wall.start, wall.end);
    const candidates: Vec2[] = [
      [foot[0] + unit[0] * halfDepth, foot[1] + unit[1] * halfDepth],
      [foot[0] - unit[0] * halfDepth, foot[1] - unit[1] * halfDepth],
    ];
    const inside = candidates.find((c) => pointInPolygon(c, polygon)) ?? candidates[0];
    best = { position: inside, yaw, wallIndex: i, distance: gap };
  }
  return best;
}

export function closestPointOnSegment(p: Vec2, a: Vec2, b: Vec2): Vec2 {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-12) return [a[0], a[1]];
  let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return [a[0] + t * dx, a[1] + t * dy];
}

/** Round a yaw to the nearest `step` degrees, per the brief's 15° snapping. */
export function snapYaw(yaw: number, step = 15): number {
  const snapped = Math.round(yaw / step) * step;
  return ((snapped % 360) + 360) % 360;
}

/** Shortest signed difference between two angles, in (-180, 180]. */
export function angleDelta(from: number, to: number): number {
  let d = ((to - from) % 360 + 540) % 360 - 180;
  if (d === -180) d = 180;
  return d;
}

/** Axis-aligned bounds of a polygon. */
export function polygonBounds(poly: Vec2[]): { min: Vec2; max: Vec2 } {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  return {
    min: [Math.min(...xs), Math.min(...ys)],
    max: [Math.max(...xs), Math.max(...ys)],
  };
}
