/**
 * Doors that open and close: the rebuilt flat's inner door leaves and the
 * shower's glass door, each its own node turning on its hinge.
 *
 * build_flat.py builds each leaf in its default pose and tags its node with
 * a `door` extra: the turns (radians about +Y, three's sense) that take it
 * from there to open and to closed, and for a doorway its centre and width
 * on the plan, so a closed door can shut that doorway to walking.
 */

import type { WalkableRoom } from './geometry';
import type { Vec2 } from './manifest';

/** A node's `door` extra, as build_flat.py writes it. */
export interface DoorSpec {
  /** The doorway's centre on the plan; absent for a door that closes no doorway (the shower). */
  at?: Vec2;
  width?: number;
  /** Turn from the built pose to fully open, and to closed. */
  open: number;
  close: number;
}

export interface DoorState extends DoorSpec {
  /** Current turn from the built pose, and the one it is heading for. */
  angle: number;
  target: number;
}

/** Seconds a door takes to swing a quarter turn. */
export const SWING_SECONDS = 0.7;

export function isDoorSpec(value: unknown): value is DoorSpec {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return typeof v.open === 'number' && typeof v.close === 'number';
}

/** A door as built: standing in its default pose. */
export function doorState(spec: DoorSpec): DoorState {
  return { ...spec, angle: 0, target: 0 };
}

export function isClosed(door: DoorState): boolean {
  return Math.abs(door.target - door.close) < 1e-6;
}

/** A click: a closed (or closing) door opens, anything else closes. */
export function toggled(door: DoorState): number {
  return isClosed(door) ? door.open : door.close;
}

/**
 * One frame of the swing towards `target`, eased at both ends. Returns the
 * new angle; it lands exactly on the target.
 */
export function swingStep(angle: number, target: number, dt: number): number {
  const rest = target - angle;
  if (Math.abs(rest) < 1e-4) return target;
  // A quarter turn in SWING_SECONDS at full speed, slowing over the last 15 degrees.
  const speed = (Math.PI / 2 / SWING_SECONDS) * Math.min(1, 0.25 + Math.abs(rest) / 0.26);
  const step = Math.min(Math.abs(rest), speed * dt);
  return angle + Math.sign(rest) * step;
}

/** How far an opening's centre may lie from a door's `at` and still be that door's. */
const SAME_DOORWAY = 0.3;

/**
 * The rooms as they can be walked with these doors closed: each room loses
 * the door openings a closed door stands in, so its wall is solid there.
 * Returns the rooms themselves when no door is closed.
 */
export function walkableWith<T extends WalkableRoom>(rooms: T[], doors: DoorState[]): T[] {
  const shut = doors.filter((d) => d.at && isClosed(d)).map((d) => d.at as Vec2);
  if (!shut.length) return rooms;
  return rooms.map((room) => {
    const openings = room.openings.filter((o) => {
      if (o.type !== 'door') return true;
      const wall = room.walls[o.wallIndex];
      if (!wall) return true;
      const len = Math.hypot(wall.end[0] - wall.start[0], wall.end[1] - wall.start[1]) || 1;
      const t = o.offset / len;
      const centre: Vec2 = [
        wall.start[0] + (wall.end[0] - wall.start[0]) * t,
        wall.start[1] + (wall.end[1] - wall.start[1]) * t,
      ];
      return !shut.some((p) => Math.hypot(p[0] - centre[0], p[1] - centre[1]) < SAME_DOORWAY);
    });
    return openings.length === room.openings.length ? room : { ...room, openings };
  });
}

/**
 * The closed doors a walk along `points` would pass through: a door whose
 * doorway centre lies within `reach` metres of the way. A glide between
 * rooms (the room strip) opens them rather than walking through a leaf.
 */
export function doorsInTheWay(doors: DoorState[], points: Vec2[], reach = 0.5): DoorState[] {
  return doors.filter((d) => {
    if (!d.at || !isClosed(d)) return false;
    for (let i = 1; i < points.length; i++) {
      const [ax, az] = points[i - 1];
      const [bx, bz] = points[i];
      const dx = bx - ax;
      const dz = bz - az;
      const len = dx * dx + dz * dz;
      const t = len ? Math.max(0, Math.min(1, ((d.at[0] - ax) * dx + (d.at[1] - az) * dz) / len)) : 0;
      if (Math.hypot(ax + dx * t - d.at[0], az + dz * t - d.at[1]) <= reach) return true;
    }
    return false;
  });
}
