import { describe, expect, it } from 'vitest';

import type { Slot } from '../src/lib/manifest';
import type { CatalogItem } from '../src/lib/store';
import {
  alternatives,
  fits,
  furnishRoom,
  kitReady,
  pieceForSlot,
  placeInSlot,
  type StyleKit,
} from '../src/lib/styles';

const piece = (id: string, category: string, w: number, d: number, styles = ['calm']): CatalogItem => ({
  id,
  name: id,
  category,
  styles,
  dimensionsM: [w, 0.8, d],
  glb: `/files/${id}.glb`,
});

const furniture: CatalogItem[] = [
  piece('sofa-large', 'sofa', 2.2, 0.95),
  piece('sofa-mid', 'sofa', 1.9, 0.9),
  piece('sofa-small', 'sofa', 1.6, 0.85),
  piece('sofa-other-kit', 'sofa', 1.7, 0.85, ['loud']),
  piece('rug', 'rug', 2.4, 1.6),
  { id: 'lamp-no-model', name: 'lamp', category: 'floor_lamp', styles: ['calm'], dimensionsM: [0.3, 1.6, 0.3] },
];

const kit: StyleKit = {
  id: 'calm',
  name: 'Calm',
  description: '',
  swatches: [],
  floorId: 'oak',
  slots: { living: { sofa: 'sofa-large', rug: 'rug', floor_lamp: 'lamp-no-model' } },
};

const slot = (category: string, maxFootprint: [number, number]): Slot => ({
  id: `${category}-1`,
  category,
  position: [0, 0, 0],
  yaw: 0,
  maxFootprint,
});

describe('slot fitting', () => {
  it("puts the kit's own piece in a slot it fits", () => {
    expect(pieceForSlot(slot('sofa', [2.4, 1.0]), kit, 'living', furniture)?.id).toBe('sofa-large');
  });

  it('falls back to the largest smaller piece of the same kit', () => {
    expect(pieceForSlot(slot('sofa', [2.0, 1.0]), kit, 'living', furniture)?.id).toBe('sofa-mid');
    expect(pieceForSlot(slot('sofa', [1.65, 0.9]), kit, 'living', furniture)?.id).toBe('sofa-small');
  });

  it("never takes another kit's piece, and leaves the slot empty when nothing fits", () => {
    expect(pieceForSlot(slot('sofa', [1.5, 1.0]), kit, 'living', furniture)).toBeNull();
  });

  it('checks width and depth against the slot as the piece faces', () => {
    expect(fits(furniture[0], slot('sofa', [2.2, 0.95]))).toBe(true);
    expect(fits(furniture[0], slot('sofa', [0.95, 2.2]))).toBe(false);
  });

  it('leaves out a piece the viewer cannot draw', () => {
    expect(pieceForSlot(slot('floor_lamp', [1, 1]), kit, 'living', furniture)).toBeNull();
  });

  it('offers a kit for a room only when it has a drawable piece for every slot', () => {
    const living = [slot('sofa', [2.4, 1]), slot('rug', [3, 2])];
    expect(kitReady(kit, 'living', living, furniture)).toBe(true);
    expect(kitReady(kit, 'living', [...living, slot('floor_lamp', [1, 1])], furniture)).toBe(false);
    expect(kitReady(kit, 'bedroom', living, furniture)).toBe(false);
    expect(kitReady(kit, 'living', [], furniture)).toBe(false);
  });

  it("lists a slot's alternatives with those that fit first, then the kit's", () => {
    const listed = alternatives(slot('sofa', [1.8, 0.9]), furniture, 'calm').map((a) => a.item.id);
    expect(listed).toEqual(['sofa-small', 'sofa-other-kit', 'sofa-large', 'sofa-mid']);
  });
});

describe('placing a piece in its slot', () => {
  // A slot 2 m deep whose front faces -z, centred on the origin: its back
  // edge is at z = +1.
  const deep: Slot = { id: 's', category: 'sofa', position: [0, 0, 0], yaw: 180, maxFootprint: [2.4, 2] };
  const sofa = piece('sofa-mid', 'sofa', 1.9, 0.9);

  it('stands the piece with its back to the back edge, turned as the slot says', () => {
    const placed = placeInSlot(sofa, deep);
    expect(placed?.yaw).toBe(180);
    expect(placed?.position[0]).toBeCloseTo(0);
    expect(placed?.position[2]).toBeCloseTo(1 - 0.45);
  });

  it('moves it forward until it clears a ceiling sloping down to the back', () => {
    // Ceiling 2.6 m at z = -1 falling to 0.6 m at z = +1: 0.8 m clears at z = 0.8.
    const slope: [number, number, number][][] = [[[-3, 2.6, -1], [3, 2.6, -1], [3, 0.6, 1], [-3, 0.6, 1]]];
    const placed = placeInSlot(sofa, deep, { ceilings: slope });
    const back = (placed?.position[2] ?? 0) + 0.45;
    expect(back).toBeLessThanOrEqual(0.8 - 0.03 + 1e-6);
    expect(back).toBeGreaterThan(0.7);
    // A slot only 1 m deep against the low end: nowhere in it clears.
    const shallow: Slot = { ...deep, position: [0, 0, 0.5], maxFootprint: [2.4, 1.0] };
    expect(placeInSlot(sofa, shallow, { ceilings: slope })).toBeNull();
  });

  it('stands a piece on another at its height', () => {
    expect(placeInSlot(sofa, deep, { base: 0.55 })?.position[1]).toBe(0.55);
  });
});


describe('furnishing a room with a kit', () => {
  const stand = piece('stand', 'nightstand', 0.5, 0.4);
  const lamp = { ...piece('lamp', 'table_lamp', 0.3, 0.3), dimensionsM: [0.3, 0.5, 0.3] as [number, number, number] };
  const bedroomKit: StyleKit = { ...kit, slots: { bedroom: { nightstand: 'stand', table_lamp: 'lamp' } } };
  const at = (id: string, category: string, on?: string): Slot => ({
    id, category, position: [0, 0, 0], yaw: 180, maxFootprint: [0.6, 0.6], ...(on ? { on } : {}),
  });

  it('stands a lamp on its nightstand, whatever order the slots come in', () => {
    const room = { type: 'bedroom' as const, slots: [at('lamp-1', 'table_lamp', 'stand-1'), at('stand-1', 'nightstand')] };
    const placed = furnishRoom(room, bedroomKit, [stand, lamp]);
    expect(placed.map((p) => p.item.id)).toEqual(['stand', 'lamp']);
    expect(placed[1].position[1]).toBeCloseTo(0.8);
  });

  it('leaves the lamp out when there is nothing to stand it on', () => {
    const room = { type: 'bedroom' as const, slots: [at('lamp-1', 'table_lamp', 'stand-1')] };
    expect(furnishRoom(room, bedroomKit, [stand, lamp])).toEqual([]);
  });
});
