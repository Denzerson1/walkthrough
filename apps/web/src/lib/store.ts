/** Zustand store: manifest, viewer state, UI state. */

import { create } from 'zustand';
import type { Manifest } from './manifest';
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
  glb?: string;
  thumbnail?: string;
  description?: string;
  palette?: string[];
  floorIds?: string[];
  picks?: Record<string, string[]>;
  license?: string;
  source?: string;
  /** Styles marked false are prepared but have no furniture picks yet. */
  complete?: boolean;
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
    styles: CatalogItem[];
  };

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
  reset: (roomId: string) => void;

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

  reset: (roomId) =>
    set((s) => {
      if (roomId === 'all') return { viewer: { ...EMPTY_VIEWER_STATE } };
      const floors = { ...s.viewer.floors };
      delete floors[roomId];
      // Recolours are keyed by manifest item id, so resetting one room has to
      // drop the recolours of the items that live in it. Leaving them behind
      // meant "restore this room" silently kept a change applied.
      const recolors = { ...s.viewer.recolors };
      for (const item of s.manifest?.items ?? []) {
        if (item.roomId === roomId) delete recolors[item.id];
      }
      return {
        viewer: {
          ...s.viewer,
          floors,
          recolors,
          items: s.viewer.items.filter((i) => i.roomId !== roomId),
        },
      };
    }),

  loadStateFromUrl: (encoded) => set({ viewer: decodeViewerState(encoded) }),

  shareUrl: () => {
    const { manifest, viewer } = get();
    const base = `${window.location.origin}/p/${manifest?.id ?? ''}`;
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
