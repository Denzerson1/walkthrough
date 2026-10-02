/**
 * Viewer state: the user's edits on top of a scene.
 *
 * Per docs/SPEC.md §4 this is a SEPARATE object from the manifest and is never
 * written back into it. It is compact enough to encode into a share URL.
 */

import type { Vec3 } from './manifest';

export interface PlacedItem {
  /** Unique per placement, so the same catalog item can be placed twice. */
  uid: string;
  /** Catalog furniture id. */
  itemId: string;
  roomId: string;
  /** Position on the floor plane in metres; y is always 0. */
  position: Vec3;
  /** Rotation about Y in degrees. */
  yaw: number;
  /** Optional recolor applied to this placed item. */
  colorHex?: string;
}

export interface ViewerState {
  /** roomId -> catalog floor id. Rooms absent from this map keep their original floor. */
  floors: Record<string, string>;
  /** Furniture the user (or the assistant) has added. */
  items: PlacedItem[];
  /** manifest item id -> colour, for recoloring furniture already in the splat (M8). */
  recolors: Record<string, string>;
  /** Room the viewer is currently standing in. */
  activeRoomId?: string;
}

export const EMPTY_VIEWER_STATE: ViewerState = {
  floors: {},
  items: [],
  recolors: {},
};

/** True when anything has been changed from the original capture. */
export function isStaged(state: ViewerState): boolean {
  return (
    Object.keys(state.floors).length > 0 ||
    state.items.length > 0 ||
    Object.keys(state.recolors).length > 0
  );
}

/**
 * True when this specific room has been modified.
 *
 * `recoloredItemIds` maps manifest items to rooms; without it a room whose
 * only change is a recolour would show no staged marker, even though the
 * global badge is up.
 */
export function isRoomStaged(
  state: ViewerState,
  roomId: string,
  recoloredItemIds: string[] = [],
): boolean {
  if (state.floors[roomId]) return true;
  if (state.items.some((i) => i.roomId === roomId)) return true;
  if (recoloredItemIds.some((id) => state.recolors[id])) return true;
  return false;
}

/**
 * Compact wire form. Keys are shortened because this goes into a URL.
 * activeRoomId is deliberately excluded: a share link should open at the
 * room the recipient chooses, not the sender's last position.
 */
interface WireState {
  f?: Record<string, string>;
  i?: Array<[string, string, number, number, number, string?]>;
  r?: Record<string, string>;
}

function toWire(state: ViewerState): WireState {
  const wire: WireState = {};
  if (Object.keys(state.floors).length) wire.f = state.floors;
  if (state.items.length) {
    wire.i = state.items.map((it) => {
      const row: [string, string, number, number, number, string?] = [
        it.uid,
        it.itemId,
        round(it.position[0]),
        round(it.position[2]),
        round(it.yaw, 1),
      ];
      if (it.colorHex) row.push(it.colorHex);
      // roomId is stored separately below to keep rows short.
      return row;
    });
  }
  if (Object.keys(state.recolors).length) wire.r = state.recolors;
  return wire;
}

function round(n: number, dp = 3): number {
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

/**
 * Items need their roomId too. Rather than complicate the tuple, encode
 * roomId as a prefix on the uid: "<roomId>|<uid>".
 */
function packUid(roomId: string, uid: string): string {
  return `${roomId}|${uid}`;
}

function unpackUid(packed: string): { roomId: string; uid: string } {
  const idx = packed.indexOf('|');
  if (idx === -1) return { roomId: '', uid: packed };
  return { roomId: packed.slice(0, idx), uid: packed.slice(idx + 1) };
}

export function encodeViewerState(state: ViewerState): string {
  const wire = toWire({
    ...state,
    items: state.items.map((it) => ({ ...it, uid: packUid(it.roomId, it.uid) })),
  });
  const json = JSON.stringify(wire);
  return base64UrlEncode(json);
}

export function decodeViewerState(encoded: string): ViewerState {
  if (!encoded) return { ...EMPTY_VIEWER_STATE };
  let wire: WireState;
  try {
    wire = JSON.parse(base64UrlDecode(encoded)) as WireState;
  } catch {
    // A corrupt share link should show the original apartment, not crash.
    return { ...EMPTY_VIEWER_STATE };
  }
  const items: PlacedItem[] = (wire.i ?? []).map((row) => {
    const [packed, itemId, x, z, yaw, colorHex] = row;
    const { roomId, uid } = unpackUid(packed);
    return {
      uid,
      itemId,
      roomId,
      position: [x, 0, z] as Vec3,
      yaw,
      ...(colorHex ? { colorHex } : {}),
    };
  });
  return {
    floors: wire.f ?? {},
    items,
    recolors: wire.r ?? {},
  };
}

function base64UrlEncode(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function base64UrlDecode(s: string): string {
  const padded = s.replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}
