import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { splitByRooms, splitFloors, tileOf } from '../src/lib/floorSplit';
import type { Room, Vec2 } from '../src/lib/manifest';

const room = (id: string, floorPolygon: Vec2[]): Room => ({
  id,
  name: id,
  type: 'other',
  waypoint: { position: [0, 1.6, 0], yaw: 0 },
  floorPolygon,
  walls: [],
  openings: [],
});

/** A level floor over [x0, x1] × [z0, z1], its texture in tiles of `tile` metres. */
function floor(x0: number, x1: number, z0: number, z1: number, tile = 1.5): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  const corners = [
    [x0, z0], [x1, z0], [x1, z1],
    [x0, z0], [x1, z1], [x0, z1],
  ];
  g.setAttribute('position', new THREE.Float32BufferAttribute(corners.flatMap(([x, z]) => [x, 0, z]), 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(corners.flatMap(([x, z]) => [x / tile, z / tile]), 2));
  return g;
}

/** Area seen from above, m². */
function area(g: THREE.BufferGeometry): number {
  const p = g.getAttribute('position');
  let total = 0;
  for (let i = 0; i < p.count; i += 3) {
    const [ax, az, bx, bz, cx, cz] = [p.getX(i), p.getZ(i), p.getX(i + 1), p.getZ(i + 1), p.getX(i + 2), p.getZ(i + 2)];
    total += Math.abs((bx - ax) * (cz - az) - (bz - az) * (cx - ax)) / 2;
  }
  return total;
}

// An L: a living room and a bedroom side by side, a hall below the living room.
const living = room('living', [[0, 0], [4, 0], [4, 5], [0, 5]]);
const bedroom = room('bedroom', [[-3, 0], [0, 0], [0, 5], [-3, 5]]);
const hall = room('hall', [[1, -2], [3, -2], [3, 0], [1, 0]]);

describe('a floor shared out among rooms', () => {
  it('gives each room exactly its own part', () => {
    const parts = splitByRooms(floor(-3, 4, 0, 5), [living, bedroom, hall]);
    expect([...parts.keys()].sort()).toEqual(['bedroom', 'living']);
    expect(area(parts.get('living')!)).toBeCloseTo(20, 6);
    expect(area(parts.get('bedroom')!)).toBeCloseTo(15, 6);
  });

  it('carries the texture over, as it lay on the whole floor', () => {
    const parts = splitByRooms(floor(-3, 4, 0, 5), [living, bedroom]);
    for (const g of parts.values()) {
      const p = g.getAttribute('position');
      const uv = g.getAttribute('uv');
      for (let i = 0; i < p.count; i++) {
        expect(uv.getX(i)).toBeCloseTo(p.getX(i) / 1.5, 6);
        expect(uv.getY(i)).toBeCloseTo(p.getZ(i) / 1.5, 6);
      }
    }
  });

  it('leaves no gap where the outlines do not quite meet, and overlaps nothing', () => {
    // The floor reaches past the rooms (under the walls), and a 2 cm strip
    // between living room and bedroom belongs to neither.
    const apart = room('bedroom', [[-3, 0], [-0.02, 0], [-0.02, 5], [-3, 5]]);
    const parts = splitByRooms(floor(-3.1, 4.1, -0.1, 5.1), [living, apart]);
    const total = [...parts.values()].reduce((sum, g) => sum + area(g), 0);
    expect(total).toBeCloseTo(7.2 * 5.2, 4); // float32 corners
    // Each has its own outline and the margin beyond its outer walls.
    expect(area(parts.get('living')!)).toBeGreaterThanOrEqual(4.1 * 5.2 - 1e-4);
    expect(area(parts.get('bedroom')!)).toBeGreaterThanOrEqual(3.08 * 5.2 - 1e-4);
  });

  it('keeps the winding, so the floor faces up as it did', () => {
    const parts = splitByRooms(floor(-3, 4, 0, 5), [living, bedroom]);
    for (const g of parts.values()) {
      const p = g.getAttribute('position');
      for (let i = 0; i < p.count; i += 3) {
        const turn = (p.getX(i + 1) - p.getX(i)) * (p.getZ(i + 2) - p.getZ(i)) -
          (p.getZ(i + 1) - p.getZ(i)) * (p.getX(i + 2) - p.getX(i));
        expect(turn).toBeGreaterThan(0);
      }
    }
  });

  it('gives a slab side standing on edge whole to the room nearest it', () => {
    const side = new THREE.BufferGeometry();
    side.setAttribute('position', new THREE.Float32BufferAttribute([2, 0, -0.1, 3, 0, -0.1, 3, -0.2, -0.1], 3));
    const parts = splitByRooms(side, [living, bedroom]);
    expect([...parts.keys()]).toEqual(['living']);
    expect(parts.get('living')!.getAttribute('position').count).toBe(3);
    expect(splitByRooms(side, [living, bedroom], { sides: false }).size).toBe(0);
  });
});

describe("a floor's tile", () => {
  it('is read off its texture coordinates', () => {
    const [w, d] = tileOf(floor(-1, 3, 0, 2, 0.8))!;
    expect(w).toBeCloseTo(0.8, 6);
    expect(d).toBeCloseTo(0.8, 6);
  });
});

describe('the model floors cut per room', () => {
  it('replaces a floor no room owns, and a zone on it, with tagged pieces', () => {
    const model = new THREE.Group();
    const main = new THREE.Mesh(floor(-3, 4, -2, 5), new THREE.MeshBasicMaterial());
    main.userData = { roomId: 'room', tileM: [1.5, 1.5] };
    const zone = new THREE.Mesh(floor(1, 3, -2, 0), new THREE.MeshBasicMaterial());
    zone.name = 'floorzone-Other';
    const own = new THREE.Mesh(floor(0, 1, 0, 1), new THREE.MeshBasicMaterial());
    own.userData = { roomId: 'living' };
    model.add(main, zone, own);

    splitFloors(model, [living, bedroom, hall]);

    const pieces = model.children.map((c) => [c.name, c.userData.roomId]);
    expect(pieces).toEqual(
      expect.arrayContaining([
        ['', 'living'],
        ['', 'bedroom'],
        ['', 'hall'],
        ['floorzone-Other', 'hall'],
      ]),
    );
    expect(model.children).toHaveLength(5);
    // Every piece knows its tile, the zone's read off its coordinates.
    for (const piece of model.children.filter((c) => c !== own)) {
      expect(piece.userData.tileM[0]).toBeCloseTo(1.5, 5);
      expect(piece.userData.tileM[1]).toBeCloseTo(1.5, 5);
    }
    // Each piece its own material, to change on its own.
    const materials = new Set(model.children.map((c) => (c as THREE.Mesh).material));
    expect(materials.size).toBe(5);
  });

  it('cuts a quantized floor where it stands, not where its small integers would', () => {
    // As gltfpack stores it: positions in [-1, 1] as int16, the node scaled up.
    const whole = floor(-3, 4, -2, 5);
    const p = whole.getAttribute('position');
    const quantized = new Int16Array(p.count * 3);
    for (let i = 0; i < p.count * 3; i++) quantized[i] = Math.round((p.array[i] / 8) * 32767);
    whole.setAttribute('position', new THREE.BufferAttribute(quantized, 3, true));
    const mesh = new THREE.Mesh(whole, new THREE.MeshBasicMaterial());
    mesh.scale.setScalar(8);
    mesh.userData = { roomId: 'room' };
    const model = new THREE.Group().add(mesh);

    splitFloors(model, [living, bedroom, hall]);

    const byRoom = Object.fromEntries(model.children.map((c) => [c.userData.roomId, area((c as THREE.Mesh).geometry)]));
    // Each room at least its own outline; the floor beside the hall that no
    // room has goes to whichever is nearest; all of it, once.
    expect(byRoom.living).toBeGreaterThanOrEqual(20 - 1e-2);
    expect(byRoom.bedroom).toBeGreaterThanOrEqual(15 - 1e-2);
    expect(byRoom.hall).toBeGreaterThanOrEqual(4 - 1e-2);
    expect(byRoom.living + byRoom.bedroom + byRoom.hall).toBeCloseTo(7 * 7, 2);
  });
});
