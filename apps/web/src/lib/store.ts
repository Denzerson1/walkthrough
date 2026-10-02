/** Zustand store: manifest, viewer state, UI state. */

import { create } from 'zustand';
import type { Manifest, PrivacyMask } from './manifest';
import {
  EMPTY_VIEWER_STATE,
  type PlacedItem,
  type ViewerState,
  decodeViewerState,
  encodeViewerState,
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
  loading: boolean;
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
  setLoading: (v: boolean) => void;
  setError: (e: string | null) => void;
  setCatalog: (c: Partial<AppState['catalog']>) => void;

  goToRoom: (roomId: string) => void;
  setPanel: (p: PanelId) => void;
  setShowingOriginal: (v: boolean) => void;

  setFloor: (roomIds: string[], floorId: string) => void;
  addItem: (item: PlacedItem) => void;
  updateItem: (uid: string, patch: Partial<PlacedItem>) => void;
  removeItem: (uid: string) => void;
  recolorItem: (itemId: string, colorHex: string) => void;
  addPrivacyMask: (mask: PrivacyMask) => void;
  reset: (roomId: string) => void;

  loadStateFromUrl: (encoded: string) => void;
  shareUrl: () => string;
}

export const useStore = create<AppState>((set, get) => ({
  manifest: null,
  loading: true,
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
      loading: false,
    })),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
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

  addItem: (item) =>
    set((s) => ({ viewer: { ...s.viewer, items: [...s.viewer.items, item] } })),

  updateItem: (uid, patch) =>
    set((s) => ({
      viewer: {
        ...s.viewer,
        items: s.viewer.items.map((i) => (i.uid === uid ? { ...i, ...patch } : i)),
      },
    })),

  removeItem: (uid) =>
    set((s) => ({
      viewer: { ...s.viewer, items: s.viewer.items.filter((i) => i.uid !== uid) },
    })),

  recolorItem: (itemId, colorHex) =>
    set((s) => ({
      viewer: { ...s.viewer, recolors: { ...s.viewer.recolors, [itemId]: colorHex } },
    })),

  addPrivacyMask: (mask) =>
    set((s) =>
      s.manifest
        ? { manifest: { ...s.manifest, privacyMasks: [...s.manifest.privacyMasks, mask] } }
        : {},
    ),

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
