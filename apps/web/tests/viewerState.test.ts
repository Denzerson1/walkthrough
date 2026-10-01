import { describe, expect, it } from 'vitest';
import {
  EMPTY_VIEWER_STATE,
  decodeViewerState,
  encodeViewerState,
  isRoomStaged,
  isStaged,
  type ViewerState,
} from '../src/lib/viewerState';

const RICH: ViewerState = {
  floors: { living: 'oak-herringbone-light', bedroom: 'tile-terrazzo-grey' },
  items: [
    {
      uid: 'a1',
      itemId: 'sofa-oslo-3',
      roomId: 'living',
      position: [1.25, 0, 2.5],
      yaw: 90,
    },
    {
      uid: 'a2',
      itemId: 'lamp-arc',
      roomId: 'living',
      position: [0.5, 0, 0.75],
      yaw: 15,
      colorHex: '#c8c2b8',
    },
  ],
  recolors: { 'sofa-1': '#2f4f4f' },
};

describe('isStaged', () => {
  it('is false for an untouched scene', () => {
    expect(isStaged(EMPTY_VIEWER_STATE)).toBe(false);
  });

  it('is true once a floor is changed', () => {
    expect(isStaged({ ...EMPTY_VIEWER_STATE, floors: { living: 'oak' } })).toBe(true);
  });

  it('is true once an item is placed', () => {
    expect(isStaged({ ...EMPTY_VIEWER_STATE, items: RICH.items })).toBe(true);
  });

  it('is true once something is recolored', () => {
    expect(isStaged({ ...EMPTY_VIEWER_STATE, recolors: { 'sofa-1': '#000' } })).toBe(true);
  });
});

describe('isRoomStaged', () => {
  it('is true for a room with a new floor', () => {
    expect(isRoomStaged(RICH, 'bedroom')).toBe(true);
  });

  it('is true for a room with placed furniture', () => {
    expect(isRoomStaged(RICH, 'living')).toBe(true);
  });

  it('is false for an untouched room', () => {
    expect(isRoomStaged(RICH, 'kitchen')).toBe(false);
  });
});

describe('share URL round trip', () => {
  it('survives encode then decode', () => {
    const decoded = decodeViewerState(encodeViewerState(RICH));
    expect(decoded.floors).toEqual(RICH.floors);
    expect(decoded.recolors).toEqual(RICH.recolors);
    expect(decoded.items).toHaveLength(2);
  });

  it('preserves item identity, room and pose', () => {
    const decoded = decodeViewerState(encodeViewerState(RICH));
    const sofa = decoded.items.find((i) => i.itemId === 'sofa-oslo-3');
    expect(sofa).toBeDefined();
    expect(sofa!.uid).toBe('a1');
    expect(sofa!.roomId).toBe('living');
    expect(sofa!.position[0]).toBeCloseTo(1.25);
    expect(sofa!.position[2]).toBeCloseTo(2.5);
    expect(sofa!.yaw).toBeCloseTo(90);
  });

  it('keeps y at zero because furniture sits on the floor', () => {
    const decoded = decodeViewerState(encodeViewerState(RICH));
    for (const item of decoded.items) expect(item.position[1]).toBe(0);
  });

  it('preserves a per-item recolor', () => {
    const decoded = decodeViewerState(encodeViewerState(RICH));
    const lamp = decoded.items.find((i) => i.itemId === 'lamp-arc');
    expect(lamp!.colorHex).toBe('#c8c2b8');
  });

  it('omits colorHex when it was never set', () => {
    const decoded = decodeViewerState(encodeViewerState(RICH));
    const sofa = decoded.items.find((i) => i.itemId === 'sofa-oslo-3');
    expect(sofa!.colorHex).toBeUndefined();
  });

  it('round-trips an empty state', () => {
    expect(decodeViewerState(encodeViewerState(EMPTY_VIEWER_STATE))).toEqual({
      floors: {},
      items: [],
      recolors: {},
    });
  });

  it('does not leak the sender position into the link', () => {
    const withRoom: ViewerState = { ...RICH, activeRoomId: 'bedroom' };
    expect(decodeViewerState(encodeViewerState(withRoom)).activeRoomId).toBeUndefined();
  });

  it('produces a URL-safe string', () => {
    const encoded = encodeViewerState(RICH);
    expect(encoded).toMatch(/^[A-Za-z0-9_-]*$/);
  });

  it('falls back to the original scene on a corrupt link', () => {
    expect(decodeViewerState('not-valid-base64!!!')).toEqual({
      floors: {},
      items: [],
      recolors: {},
    });
  });

  it('treats an empty string as an unmodified scene', () => {
    expect(isStaged(decodeViewerState(''))).toBe(false);
  });

  it('handles room ids containing the separator character', () => {
    const odd: ViewerState = {
      floors: {},
      recolors: {},
      items: [
        { uid: 'u|1', itemId: 'chair', roomId: 'living', position: [0, 0, 0], yaw: 0 },
      ],
    };
    const decoded = decodeViewerState(encodeViewerState(odd));
    expect(decoded.items[0].roomId).toBe('living');
    expect(decoded.items[0].uid).toBe('u|1');
  });
});
