/** Rooms as a visitor meets them: where the way into each one is, and how big it is. */

import { pointInPolygon, polygonArea } from './geometry';
import type { Room, Vec2 } from './manifest';

/** How far apart two outlines' edges may lie and still be one boundary, metres. */
const SHARED = 0.05;
/** The narrowest opening a person walks through, metres: less is two corners touching. */
const MIN_OPENING = 0.6;

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
    if (!shared) return [];
    const [a, b] = shared;
    return Math.hypot(b[0] - a[0], b[1] - a[1]) >= MIN_OPENING ? [{ roomId: room.id, along: shared }] : [];
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

/** How far either side of a narrow opening the way runs square to it, metres. */
const APPROACH = 0.35;
/** Narrower than this, an opening is a door, crossed square rather than at a slant. */
const DOOR = 1.2;
/** How far from an opening's ends the way keeps, metres. */
const JAMB = 0.45;
/** Points closer than this to the one before are dropped: a curve kinks at them. */
const MIN_STEP = 0.25;

/**
 * The points to pass on the way from `from` in one room to `to` in another,
 * through the openings of the fewest rooms between: at the place in each
 * that makes the shortest way, clear of its ends, and through a door square
 * (a step before it and one after) rather than at a slant past its jamb.
 * Rooms with no way between them are joined directly.
 */
export function route(rooms: Room[], fromId: string, toId: string, from: Vec2, to: Vec2): Vec2[] {
  const points: Vec2[] = [from];
  const add = (p: Vec2) => {
    const last = points[points.length - 1];
    if (Math.hypot(p[0] - last[0], p[1] - last[1]) >= MIN_STEP) points.push(p);
  };
  const hops = roomsBetween(rooms, fromId, toId);
  for (let i = 1; i < hops.length; i++) {
    const along = sharedStretch(hops[i - 1].floorPolygon, hops[i].floorPolygon);
    if (!along) continue;
    const [a, b] = along;
    const width = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const door = shortestThrough(along, points[points.length - 1], to);
    if (width >= DOOR) {
      add(door);
      continue;
    }
    // Across the opening, pointing into the next room.
    let n: Vec2 = [-(b[1] - a[1]) / width, (b[0] - a[0]) / width];
    if (!pointInPolygon([door[0] + n[0] * 0.1, door[1] + n[1] * 0.1], hops[i].floorPolygon)) n = [-n[0], -n[1]];
    add([door[0] - n[0] * APPROACH, door[1] - n[1] * APPROACH]);
    // No step beyond it when the way ends just past it: the curve would hook back.
    if (Math.hypot(to[0] - door[0], to[1] - door[1]) > 2 * APPROACH) {
      add([door[0] + n[0] * APPROACH, door[1] + n[1] * APPROACH]);
    }
  }
  // The end is where it must be, even right after an opening.
  add(to);
  if (points[points.length - 1] !== to) {
    if (points.length > 1) points[points.length - 1] = to;
    else points.push(to);
  }
  return points;
}

/** The point of an opening, clear of its ends, that makes the shortest way from `p` to `q`. */
function shortestThrough(along: [Vec2, Vec2], p: Vec2, q: Vec2): Vec2 {
  const [a, b] = along;
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const pad = Math.min(JAMB, length / 2);
  let best: Vec2 = a;
  let shortest = Infinity;
  for (let i = 0; i <= 40; i++) {
    const t = (pad + ((length - 2 * pad) * i) / 40) / length;
    const c: Vec2 = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const way = Math.hypot(c[0] - p[0], c[1] - p[1]) + Math.hypot(q[0] - c[0], q[1] - c[1]);
    if (way < shortest) {
      shortest = way;
      best = c;
    }
  }
  return best;
}
/** The rooms passed through from one to the other, both included (breadth first). */
function roomsBetween(rooms: Room[], fromId: string, toId: string): Room[] {
  const byId = new Map(rooms.map((r) => [r.id, r]));
  const start = byId.get(fromId);
  const goal = byId.get(toId);
  if (!start || !goal) return [];
  const came = new Map<string, string | null>([[start.id, null]]);
  const queue = [start];
  while (queue.length) {
    const room = queue.shift() as Room;
    if (room.id === goal.id) break;
    for (const { roomId } of doorways(rooms, room.id)) {
      if (came.has(roomId)) continue;
      came.set(roomId, room.id);
      queue.push(byId.get(roomId) as Room);
    }
  }
  if (!came.has(goal.id)) return [start, goal];
  const path: Room[] = [];
  for (let id: string | null = goal.id; id; id = came.get(id) ?? null) path.unshift(byId.get(id) as Room);
  return path;
}
