/**
 * Style kits and furniture slots: which catalog piece stands in which slot,
 * and where exactly.
 *
 * A room's slots (manifest Room.slots) say where a piece of each category
 * goes and how much floor it may take. A kit (catalog/styles) names one piece
 * per category for each room type. Applying a kit puts its piece in every
 * slot, or the next smaller piece of the same kit where its own does not fit,
 * or leaves the slot empty. Pure functions, shared by the menu, 3D taps and
 * the assistant, so all three place the same pieces in the same spots.
 */

import { ceilingHeight } from './geometry';
import type { RoomType, Slot, Vec2, Vec3 } from './manifest';
import type { CatalogItem } from './store';

/** catalog/styles/<id>.json */
export interface StyleKit {
  id: string;
  name: string;
  description: string;
  /** Three colours that say what the kit looks like. */
  swatches: string[];
  /** The catalog floor that goes with it. */
  floorId: string;
  /** Room type -> slot category -> catalog furniture id. */
  slots: Partial<Record<RoomType, Record<string, string>>>;
}

/** What a slot's room says about the space above it. */
export interface SlotSpace {
  /** The room's ceiling pieces (Room.ceilings): a piece must stand under them. */
  ceilings?: Vec3[][];
  /** How high the piece stands: the top of what it stands on (Slot.on), else 0. */
  base?: number;
}

/** Room a piece keeps under a sloping ceiling, metres. */
const HEAD_CLEARANCE = 0.03;
/** How far at a time a piece is moved out from under a slope, metres. */
const STEP = 0.02;

/**
 * Where `item` stands in `slot`: its back to the slot's back edge (the edge
 * its front faces away from), moved forward only as far as it must to clear
 * a sloping ceiling, and still within the slot. Null when it does not fit.
 */
export function placeInSlot(
  item: CatalogItem,
  slot: Slot,
  space: SlotSpace = {},
): { position: Vec3; yaw: number } | null {
  const [w, h, d] = item.dimensionsM ?? [0, 0, 0];
  const [maxW, maxD] = slot.maxFootprint;
  if (w > maxW + 1e-6 || d > maxD + 1e-6) return null;
  const yaw = (slot.yaw * Math.PI) / 180;
  // A yaw of `w` points the front along (-sin w, cos w) (layout.py facing_yaw).
  const front: Vec2 = [-Math.sin(yaw), Math.cos(yaw)];
  const side: Vec2 = [front[1], -front[0]];
  const [cx, , cz] = slot.position;
  const top = (space.base ?? 0) + h;
  for (let shift = 0; shift <= maxD - d + 1e-6; shift += STEP) {
    const out = -maxD / 2 + d / 2 + shift;
    const centre: Vec2 = [cx + front[0] * out, cz + front[1] * out];
    const corners: Vec2[] = [-1, 1].flatMap((a) =>
      [-1, 1].map((b): Vec2 => [
        centre[0] + (side[0] * a * w) / 2 + (front[0] * b * d) / 2,
        centre[1] + (side[1] * a * w) / 2 + (front[1] * b * d) / 2,
      ]),
    );
    const clear = [centre, ...corners].every((p) => {
      const ceiling = space.ceilings?.length ? ceilingHeight(p, space.ceilings) : undefined;
      return ceiling === undefined || ceiling >= top + HEAD_CLEARANCE;
    });
    if (clear) return { position: [centre[0], space.base ?? 0, centre[1]], yaw: slot.yaw };
  }
  return null;
}

/** Whether `item` fits `slot`: on its floor and under its ceiling. */
export function fits(item: CatalogItem, slot: Slot, space: SlotSpace = {}): boolean {
  return placeInSlot(item, slot, space) !== null;
}

const area = (item: CatalogItem) => (item.dimensionsM?.[0] ?? 0) * (item.dimensionsM?.[2] ?? 0);

/** A piece the viewer can draw. */
function drawable(item: CatalogItem | undefined): item is CatalogItem {
  return Boolean(item?.glb);
}

/**
 * The kit's piece for `slot` in a room of `roomType`: its own if it fits,
 * else the largest smaller piece of the same kit and category that does,
 * else null (the slot stays empty).
 */
export function pieceForSlot(
  slot: Slot,
  kit: StyleKit,
  roomType: RoomType,
  furniture: CatalogItem[],
  space: SlotSpace = {},
): CatalogItem | null {
  const own = furniture.find((f) => f.id === kit.slots[roomType]?.[slot.category]);
  if (drawable(own) && fits(own, slot, space)) return own;
  const limit = own ? area(own) : Infinity;
  const smaller = furniture
    .filter(
      (f) =>
        drawable(f) &&
        f.category === slot.category &&
        f.styles?.includes(kit.id) &&
        area(f) < limit &&
        fits(f, slot, space),
    )
    .sort((a, b) => area(b) - area(a));
  return smaller[0] ?? null;
}

/**
 * Whether a kit can furnish a room of `roomType` with these slots: it names
 * a drawable piece for every slot's category. Kits still missing one are not
 * offered for that room.
 */
export function kitReady(
  kit: StyleKit,
  roomType: RoomType,
  slots: Slot[],
  furniture: CatalogItem[],
): boolean {
  const picks = kit.slots[roomType];
  if (!picks || !slots.length) return false;
  return slots.every((s) => drawable(furniture.find((f) => f.id === picks[s.category])));
}

/**
 * The pieces that could stand in `slot`: its category's, those that fit
 * first, then the current kit's, then by name.
 */
export function alternatives(
  slot: Slot,
  furniture: CatalogItem[],
  kitId?: string,
  space: SlotSpace = {},
): Array<{ item: CatalogItem; fits: boolean }> {
  return furniture
    .filter((f) => f.category === slot.category && drawable(f))
    .map((item) => ({ item, fits: fits(item, slot, space) }))
    .sort(
      (a, b) =>
        Number(b.fits) - Number(a.fits) ||
        Number(Boolean(kitId && b.item.styles?.includes(kitId))) -
          Number(Boolean(kitId && a.item.styles?.includes(kitId))) ||
        a.item.name.localeCompare(b.item.name),
    );
}

/** A piece as a kit or a swap stands it in a slot. */
export interface SlotPlacement {
  slot: Slot;
  item: CatalogItem;
  position: Vec3;
  yaw: number;
}

/** Slots in the order they can be filled: a piece before what stands on it. */
function supportsFirst(slots: Slot[]): Slot[] {
  return [...slots].sort((a, b) => Number(Boolean(a.on)) - Number(Boolean(b.on)));
}

/**
 * `room` furnished with `kit`: each slot's piece (pieceForSlot) where it
 * stands, a lamp on its nightstand at the nightstand's height. Slots left
 * empty are left out.
 */
export function furnishRoom(
  room: { type: RoomType; slots?: Slot[]; ceilings?: Vec3[][] },
  kit: StyleKit,
  furniture: CatalogItem[],
): SlotPlacement[] {
  const placed: SlotPlacement[] = [];
  for (const slot of supportsFirst(room.slots ?? [])) {
    const space = { ceilings: room.ceilings, base: baseFor(slot, placed) };
    if (space.base === null) continue; // what it stands on stayed empty
    const item = pieceForSlot(slot, kit, room.type, furniture, { ...space, base: space.base });
    const at = item && placeInSlot(item, slot, { ...space, base: space.base });
    if (item && at) placed.push({ slot, item, ...at });
  }
  return placed;
}

/**
 * How high a piece in `slot` stands: on the floor, or on top of the piece in
 * the slot it stands on (Slot.on); null when that slot is empty.
 */
export function baseFor(slot: Slot, placed: Array<{ slot: Slot; item: CatalogItem }>): number | null {
  if (!slot.on) return 0;
  const under = placed.find((p) => p.slot.id === slot.on);
  return under ? (under.item.dimensionsM?.[1] ?? 0) : null;
}
