/** Zustand store: manifest, viewer state, UI state. */

import { create } from 'zustand';
import type { Manifest, Room, Vec3 } from './manifest';
import { baseFor, furnishRoom, placeInSlot, type StyleKit } from './styles';
import {
  EMPTY_VIEWER_STATE,
  type PlacedItem,
  type ViewerState,
  decodeViewerState,
  encodeViewerState,
  newUid,
} from './viewerState';

export type PanelId = 'floors' | 'furniture' | 'assistant' | null;

export interface CatalogItem {
  id: string;
  name: string;
  category?: string;
  tags?: string[];
  styles?: string[];
  colors?: string[];
  tileSizeM?: [number, number];
  /** Representative colour used until the texture maps are downloaded. */
  baseColor?: string;
  dimensionsM?: [number, number, number];
  maps?: Record<string, string>;
  /** Floors whose catalog colour tints a neutral scan (painted, stencilled). */
  tintAlbedo?: boolean;
  /** The model for a desktop; `glbPhone` the lighter one for a phone (prepare-models.mjs). */
  glb?: string | null;
  glbPhone?: string | null;
  thumbnail?: string;
  description?: string;
  license?: string;
  /** Who to credit, for a CC BY model. */
  attribution?: string;
  source?: string;
}

/** A change the person can take back from the toast that announces it. */
export interface Toast {
  id: number;
  message: string;
  /** The viewer state before the change; Undo restores it. */
  before?: ViewerState;
}

interface AppState {
  manifest: Manifest | null;
  error: string | null;

  viewer: ViewerState;
  activeRoomId: string | null;
  panel: PanelId;

  catalog: {
    floors: CatalogItem[];
    furniture: CatalogItem[];
    styles: StyleKit[];
  };

  toast: Toast | null;

  /** Set when the user is comparing against the original capture. */
  showingOriginal: boolean;

  setManifest: (m: Manifest) => void;
  setError: (e: string | null) => void;
  setCatalog: (c: Partial<AppState['catalog']>) => void;

  goToRoom: (roomId: string) => void;
  setPanel: (p: PanelId) => void;
  setShowingOriginal: (v: boolean) => void;

  setFloor: (roomIds: string[], floorId: string) => void;
  /** `uid` is assigned here, so callers describe the item and nothing else. */
  addItems: (items: Array<Omit<PlacedItem, 'uid'>>) => void;
  recolorItem: (itemId: string, colorHex: string) => void;
  /** Drag a placed item to a new spot on the floor. */
  moveItem: (uid: string, position: Vec3, roomId?: string) => void;
  /** Turn a placed item by `delta` degrees. */
  rotateItem: (uid: string, delta: number) => void;
  removeItem: (uid: string) => void;
  /** Drop every placed item in a room, before furnishing it afresh. */
  clearItems: (roomId: string) => void;
  reset: (roomId: string) => void;

  /**
   * Furnish rooms with a style kit: its piece in every slot (styles.ts), its
   * floor, replacing what an earlier kit put there. 'all' is every room the
   * kit can furnish.
   */
  applyStyle: (roomIds: string[] | 'all', styleId: string) => void;
  /** Stand another catalog piece in a room's slot, re-standing what stands on it. */
  swapPiece: (roomId: string, slotId: string, itemId: string) => void;
  /** Take back the change the toast announced. */
  undo: () => void;
  dismissToast: () => void;

  /** Placed item the user has tapped, for the move/rotate controls. */
  selectedUid: string | null;
  selectItem: (uid: string | null) => void;

  loadStateFromUrl: (encoded: string) => void;
  shareUrl: () => string;
}

export const useStore = create<AppState>((set, get) => ({
  manifest: null,
  error: null,

  viewer: { ...EMPTY_VIEWER_STATE },
  activeRoomId: null,
  panel: null,

  catalog: { floors: [], furniture: [], styles: [] },
  showingOriginal: false,
  selectedUid: null,
  toast: null,

  setManifest: (manifest) =>
    set((s) => ({
      manifest,
      activeRoomId: s.activeRoomId ?? manifest.rooms[0]?.id ?? null,
    })),
  setError: (error) => set({ error }),
  setCatalog: (c) => set((s) => ({ catalog: { ...s.catalog, ...c } })),

  goToRoom: (roomId) => set({ activeRoomId: roomId }),
  setPanel: (panel) => set((s) => ({ panel: s.panel === panel ? null : panel })),
  setShowingOriginal: (showingOriginal) => set({ showingOriginal }),

  setFloor: (roomIds, floorId) =>
    set((s) => {
      const all = roomIds.includes('all');
      const targets = all ? (s.manifest?.rooms ?? []).map((r) => r.id) : roomIds;
      const floors = { ...s.viewer.floors };
      for (const id of targets) floors[id] = floorId;
      return { viewer: { ...s.viewer, floors } };
    }),

  // One set() for the whole batch. Adding items one at a time made the
  // furniture effect dispose and rebuild every mesh per item, so placing k
  // items built k(k+1)/2 meshes to end up with k.
  addItems: (items) =>
    set((s) => ({
      viewer: {
        ...s.viewer,
        items: [...s.viewer.items, ...items.map((i) => ({ ...i, uid: newUid() }))],
      },
    })),

  recolorItem: (itemId, colorHex) =>
    set((s) => ({
      viewer: { ...s.viewer, recolors: { ...s.viewer.recolors, [itemId]: colorHex } },
    })),

  moveItem: (uid, position, roomId) =>
    set((s) => ({
      viewer: {
        ...s.viewer,
        items: s.viewer.items.map((i) =>
          i.uid === uid ? { ...i, position, roomId: roomId ?? i.roomId } : i,
        ),
      },
    })),

  rotateItem: (uid, delta) =>
    set((s) => ({
      viewer: {
        ...s.viewer,
        items: s.viewer.items.map((i) =>
          i.uid === uid ? { ...i, yaw: (((i.yaw + delta) % 360) + 360) % 360 } : i,
        ),
      },
    })),

  removeItem: (uid) =>
    set((s) => ({
      selectedUid: s.selectedUid === uid ? null : s.selectedUid,
      viewer: { ...s.viewer, items: s.viewer.items.filter((i) => i.uid !== uid) },
    })),

  clearItems: (roomId) =>
    set((s) => ({
      selectedUid: null,
      viewer: { ...s.viewer, items: s.viewer.items.filter((i) => i.roomId !== roomId) },
    })),

  selectItem: (selectedUid) => set({ selectedUid }),

  reset: (roomId) =>
    set((s) => {
      if (roomId === 'all') return { viewer: { ...EMPTY_VIEWER_STATE }, selectedUid: null };
      const floors = { ...s.viewer.floors };
      delete floors[roomId];
      const styles = { ...s.viewer.styles };
      delete styles[roomId];
      // Recolours are keyed by manifest item id, so resetting one room has to
      // drop the recolours of the items that live in it. Leaving them behind
      // meant "restore this room" silently kept a change applied.
      const recolors = { ...s.viewer.recolors };
      for (const item of s.manifest?.items ?? []) {
        if (item.roomId === roomId) delete recolors[item.id];
      }
      return {
        selectedUid: null,
        viewer: {
          ...s.viewer,
          floors,
          recolors,
          styles,
          items: s.viewer.items.filter((i) => i.roomId !== roomId),
        },
      };
    }),

  applyStyle: (roomIds, styleId) =>
    set((s) => {
      const kit = s.catalog.styles.find((k) => k.id === styleId);
      const rooms = (s.manifest?.rooms ?? []).filter(
        (r) => roomIds === 'all' || roomIds.includes(r.id),
      );
      if (!kit || !rooms.length) return {};
      let viewer = s.viewer;
      const furnished: Room[] = [];
      for (const room of rooms) {
        const placed = furnishRoom(room, kit, s.catalog.furniture);
        if (!placed.length) continue;
        furnished.push(room);
        viewer = {
          ...viewer,
          floors: { ...viewer.floors, [room.id]: kit.floorId },
          styles: { ...viewer.styles, [room.id]: kit.id },
          items: [
            ...viewer.items.filter((i) => !(i.roomId === room.id && i.slotId)),
            ...placed.map((p) => ({
              uid: newUid(),
              itemId: p.item.id,
              roomId: room.id,
              slotId: p.slot.id,
              position: p.position,
              yaw: p.yaw,
            })),
          ],
        };
      }
      const where =
        roomIds === 'all' ? 'every room' : furnished.map((r) => r.name).join(', ') || rooms[0].name;
      return {
        viewer,
        selectedUid: null,
        toast: furnished.length
          ? { id: Date.now(), message: `${kit.name} · ${where}`, before: s.viewer }
          : { id: Date.now(), message: `${kit.name} has no pieces for ${where} yet` },
      };
    }),

  swapPiece: (roomId, slotId, itemId) =>
    set((s) => {
      const room = s.manifest?.rooms.find((r) => r.id === roomId);
      const item = s.catalog.furniture.find((f) => f.id === itemId);
      const slot = room?.slots?.find((sl) => sl.id === slotId);
      if (!room || !item || !slot) return {};
      const inRoom = s.viewer.items.filter((i) => i.roomId === roomId && i.slotId);
      // What stands in each slot once the swap is made, for the heights of
      // pieces standing on others.
      const standing = (room.slots ?? []).flatMap((sl) => {
        const id = sl.id === slotId ? itemId : inRoom.find((i) => i.slotId === sl.id)?.itemId;
        const piece = s.catalog.furniture.find((f) => f.id === id);
        return piece ? [{ slot: sl, item: piece }] : [];
      });
      const space = (sl: typeof slot) => ({ ceilings: room.ceilings, base: baseFor(sl, standing) });
      const restand = (sl: typeof slot, piece: CatalogItem) => {
        const base = space(sl).base;
        return base === null ? null : placeInSlot(piece, sl, { ceilings: room.ceilings, base });
      };
      const at = restand(slot, item);
      if (!at) return { toast: { id: Date.now(), message: `${item.name} does not fit there` } };
      const items = s.viewer.items.flatMap((i) => {
        if (i.roomId !== roomId || !i.slotId) return [i];
        if (i.slotId === slotId) return [];
        const sl = room.slots?.find((x) => x.id === i.slotId);
        if (sl?.on !== slotId) return [i];
        // A lamp on the nightstand just swapped: on the new one, or gone.
        const piece = s.catalog.furniture.find((f) => f.id === i.itemId);
        const moved = piece && restand(sl, piece);
        return moved ? [{ ...i, position: moved.position }] : [];
      });
      const uid = newUid();
      items.push({ uid, itemId, roomId, slotId, position: at.position, yaw: at.yaw });
      return {
        viewer: { ...s.viewer, items },
        selectedUid: uid,
        toast: { id: Date.now(), message: item.name, before: s.viewer },
      };
    }),

  undo: () =>
    set((s) => (s.toast?.before ? { viewer: s.toast.before, toast: null, selectedUid: null } : {})),

  dismissToast: () => set({ toast: null }),

  loadStateFromUrl: (encoded) => set({ viewer: decodeViewerState(encoded), selectedUid: null }),

  shareUrl: () => {
    const { manifest, viewer } = get();
    const base = `${window.location.origin}${import.meta.env.BASE_URL}p/${manifest?.id ?? ''}`;
    const encoded = encodeViewerState(viewer);
    return encoded ? `${base}?s=${encoded}` : base;
  },
}));

/**
 * Rooms the viewer has changed, for the per-room staged markers.
 *
 * Takes the manifest items so a recoloured piece of existing furniture marks
 * the room it stands in.
 */
export function stagedRoomIds(
  state: ViewerState,
  manifestItems: Array<{ id: string; roomId: string }> = [],
): Set<string> {
  const ids = new Set<string>(Object.keys(state.floors));
  for (const item of state.items) ids.add(item.roomId);
  for (const item of manifestItems) {
    if (state.recolors[item.id]) ids.add(item.roomId);
  }
  return ids;
}
