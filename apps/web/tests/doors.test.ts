import { describe, expect, it } from 'vitest';
import { canStand, type WalkableRoom } from '../src/lib/geometry';
import type { Vec2 } from '../src/lib/manifest';
import {
  doorState,
  doorsInTheWay,
  isClosed,
  isDoorSpec,
  swingStep,
  toggled,
  walkableWith,
  type DoorState,
} from '../src/lib/doors';

/** Two rooms sharing the wall x = 4, a 0.9 m door centred at z = 1.5. */
const square = (x0: number, x1: number): WalkableRoom => ({
  floorPolygon: [
    [x0, 0],
    [x1, 0],
    [x1, 3],
    [x0, 3],
  ],
  walls: [
    { start: [x0, 0], end: [x1, 0] },
    { start: [x1, 0], end: [x1, 3] },
    { start: [x1, 3], end: [x0, 3] },
    { start: [x0, 3], end: [x0, 0] },
  ],
  openings: [],
});
const left: WalkableRoom = {
  ...square(0, 4),
  openings: [{ type: 'door', wallIndex: 1, offset: 1.5, width: 0.9 }],
};
const right: WalkableRoom = {
  ...square(4, 7),
  openings: [{ type: 'door', wallIndex: 3, offset: 1.5, width: 0.9 }],
};
const doorway: Vec2 = [4, 1.5];

const leaf = (target: 'open' | 'closed'): DoorState => {
  const door = doorState({ at: [4, 1.5], width: 0.9, open: 0, close: Math.PI / 2 });
  return { ...door, target: target === 'open' ? door.open : door.close };
};

describe('doors', () => {
  it('reads a door extra and nothing else', () => {
    expect(isDoorSpec({ open: 0, close: 1.57, at: [1, 2] })).toBe(true);
    expect(isDoorSpec({ tileM: [1, 1] })).toBe(false);
    expect(isDoorSpec(undefined)).toBe(false);
  });

  it('opens a closed door and closes anything else', () => {
    const open = leaf('open');
    expect(isClosed(open)).toBe(false);
    expect(toggled(open)).toBe(open.close);
    const shut = leaf('closed');
    expect(isClosed(shut)).toBe(true);
    expect(toggled(shut)).toBe(shut.open);
    // The shower stands half open: a click shuts it first.
    const shower = doorState({ open: -Math.PI / 4, close: Math.PI / 4 });
    expect(toggled(shower)).toBe(Math.PI / 4);
  });

  it('swings towards its target and lands on it', () => {
    let angle = 0;
    for (let i = 0; i < 200 && angle !== Math.PI / 2; i++) angle = swingStep(angle, Math.PI / 2, 1 / 60);
    expect(angle).toBe(Math.PI / 2);
    expect(swingStep(1, 0, 1 / 60)).toBeLessThan(1);
  });

  it('lets you through an open door and not a closed one', () => {
    expect(canStand(doorway, walkableWith([left, right], [leaf('open')]))).toBe(true);
    expect(canStand(doorway, walkableWith([left, right], [leaf('closed')]))).toBe(false);
  });

  it('shuts only the doorway the door stands in', () => {
    const elsewhere = { ...leaf('closed'), at: [0, 1.5] as Vec2 };
    expect(canStand(doorway, walkableWith([left, right], [elsewhere]))).toBe(true);
    const rooms = [left, right];
    expect(walkableWith(rooms, [])).toBe(rooms);
  });

  it('finds the closed doors a glide would pass through', () => {
    const shut = leaf('closed');
    const across: Vec2[] = [[3, 1.5], [5, 1.5]];
    const along: Vec2[] = [[1, 0.5], [3, 0.5]];
    expect(doorsInTheWay([shut], across)).toEqual([shut]);
    expect(doorsInTheWay([shut], along)).toEqual([]);
    expect(doorsInTheWay([leaf('open')], across)).toEqual([]);
  });
});
