import { beforeEach, describe, expect, it } from 'vitest';

import type { Manifest, Room } from '../src/lib/manifest';
import { useStore } from '../src/lib/store';
import type { StyleKit } from '../src/lib/styles';
import { EMPTY_VIEWER_STATE } from '../src/lib/viewerState';

const slot = (id: string, category: string, on?: string) => ({
  id, category, position: [0, 0, 0] as [number, number, number], yaw: 0, maxFootprint: [2, 2] as [number, number], ...(on ? { on } : {}),
});
const bedroom: Room = {
  id: 'bedroom',
  name: 'Bedroom',
  type: 'bedroom',
  waypoint: { position: [0, 1.6, 0], yaw: 0 },
  floorPolygon: [[0, 0], [3, 0], [3, 3], [0, 3]],
  walls: [],
  openings: [],
  slots: [slot('stand', 'nightstand'), slot('lamp', 'table_lamp', 'stand'), slot('rug', 'rug')],
};
const kit = { id: 'calm', name: 'Calm', floorId: 'oak', swatches: [], slots: {} } as unknown as StyleKit;
const at = (slotId: string) => ({
  uid: slotId, itemId: `${slotId}-piece`, roomId: 'bedroom', slotId, position: [0, 0, 0] as [number, number, number], yaw: 0,
});

beforeEach(() => {
  useStore.setState({
    manifest: { id: 'flat', rooms: [bedroom], items: [] } as unknown as Manifest,
    catalog: { floors: [], furniture: [], styles: [kit], finishes: [] },
    viewer: {
      ...EMPTY_VIEWER_STATE,
      floors: { bedroom: 'oak' },
      styles: { bedroom: 'calm' },
      finishes: { kitchen: 'kitchen-sage' },
      items: [at('stand'), at('lamp'), at('rug')],
    },
    toast: null,
  });
});

describe('the design store', () => {
  it('resets everything, and Undo brings it all back', () => {
    const before = useStore.getState().viewer;
    useStore.getState().reset('all');
    expect(useStore.getState().viewer).toEqual(EMPTY_VIEWER_STATE);
    useStore.getState().undo();
    expect(useStore.getState().viewer).toEqual(before);
  });

  it("takes a style out: its pieces and the floor it laid, not the kitchen's finish", () => {
    useStore.getState().removeStyle();
    const { viewer } = useStore.getState();
    expect(viewer.items).toEqual([]);
    expect(viewer.styles).toEqual({});
    expect(viewer.floors).toEqual({});
    expect(viewer.finishes).toEqual({ kitchen: 'kitchen-sage' });
  });

  it('keeps a floor laid apart from the style', () => {
    useStore.setState((s) => ({ viewer: { ...s.viewer, floors: { bedroom: 'slate' } } }));
    useStore.getState().removeStyle();
    expect(useStore.getState().viewer.floors).toEqual({ bedroom: 'slate' });
  });

  it('empties a slot with what stands on it', () => {
    useStore.getState().clearSlot('bedroom', 'stand');
    expect(useStore.getState().viewer.items.map((i) => i.slotId)).toEqual(['rug']);
  });
});
