/** Rooms as a visitor meets them: where the way into each one is, and how big it is. */

import { polygonArea } from './geometry';
import type { Room, Vec2 } from './manifest';

/** How far apart two outlines' edges may lie and still be one boundary, metres. */
const SHARED = 0.05;

/** The room's floor area: the listing's figure, else its outline's. */
export function roomArea(room: Pick<Room, 'areaM2' | 'floorPolygon'>): number {
  return room.areaM2 ?? Math.abs(polygonArea(room.floorPolygon));
}

/**
 * The way into each room next to `current`: the longest stretch of outline
 * they share (the opening between them), for the rooms that share one.
 * Rooms that do not border `current` are not near enough to label.
 */
export function doorways(
  rooms: Room[],
  currentId: string,
): Array<{ roomId: string; along: [Vec2, Vec2] }> {
  const current = rooms.find((r) => r.id === currentId);
  if (!current) return [];
  return rooms.flatMap((room) => {
    if (room.id === currentId) return [];
    const shared = sharedStretch(current.floorPolygon, room.floorPolygon);
    return shared ? [{ roomId: room.id, along: shared }] : [];
  });
}

/** The longest stretch two outlines share, as its two ends, or null. */
export function sharedStretch(a: Vec2[], b: Vec2[]): [Vec2, Vec2] | null {
  let best: { length: number; ends: [Vec2, Vec2] } | null = null;
  for (let i = 0; i < a.length; i++) {
    const p = a[i];
    const q = a[(i + 1) % a.length];
    const length = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (length < 1e-6) continue;
    const u: Vec2 = [(q[0] - p[0]) / length, (q[1] - p[1]) / length];
    for (let j = 0; j < b.length; j++) {
      const r = b[j];
      const s = b[(j + 1) % b.length];
      // Both ends of b's edge on the line of a's edge?
      const off = (v: Vec2) => Math.abs((v[0] - p[0]) * u[1] - (v[1] - p[1]) * u[0]);
      if (off(r) > SHARED || off(s) > SHARED) continue;
      const along = (v: Vec2) => (v[0] - p[0]) * u[0] + (v[1] - p[1]) * u[1];
      const lo = Math.max(0, Math.min(along(r), along(s)));
      const hi = Math.min(length, Math.max(along(r), along(s)));
      if (hi - lo <= SHARED || (best && hi - lo <= best.length)) continue;
      const at = (t: number): Vec2 => [p[0] + u[0] * t, p[1] + u[1] * t];
      best = { length: hi - lo, ends: [at(lo), at(hi)] };
    }
  }
  return best?.ends ?? null;
}

/**
 * Where on a way in to show its label: the point of `along` nearest `p`,
 * kept `inset` from its ends, so the label stands where you would walk
 * through rather than at the far end of a long opening.
 */
export function nearestOnStretch(along: [Vec2, Vec2], p: Vec2, inset = 0.4): Vec2 {
  const [a, b] = along;
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (length < 1e-9) return a;
  const u: Vec2 = [(b[0] - a[0]) / length, (b[1] - a[1]) / length];
  const pad = Math.min(inset, length / 2);
  const t = Math.min(Math.max((p[0] - a[0]) * u[0] + (p[1] - a[1]) * u[1], pad), length - pad);
  return [a[0] + u[0] * t, a[1] + u[1] * t];
}
