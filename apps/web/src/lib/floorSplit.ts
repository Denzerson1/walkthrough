/**
 * A floor built whole for an open plan, cut along the rooms drawn on it
 * afterwards (pipeline `rooms`), so that each room can take a floor of its
 * own. Every triangle is cut along the rooms' edges into convex pieces, and
 * each piece goes to the room it lies in, or to the nearest one: the pieces
 * cover the floor exactly once, with no gap and no overlap, whether or not
 * the rooms' outlines quite meet.
 */

import * as THREE from 'three';
import { distancePointToSegment, roomAt } from './geometry';
import type { Room, Vec2 } from './manifest';

/** Below this, in m² (twice the area), a piece or a triangle seen from above is a line. */
const FLAT = 1e-9;

const cross = (o: Vec2, a: Vec2, b: Vec2) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

/** A point inside a convex piece. */
const middleOf = (piece: Vec2[]): Vec2 =>
  piece.reduce<Vec2>((m, p) => [m[0] + p[0] / piece.length, m[1] + p[1] / piece.length], [0, 0]);

/**
 * The geometry's triangles one after another, every attribute in plain
 * floats. A model's are often quantized (KHR_mesh_quantization: small
 * integers, scaled by the node), which moving them into place would clamp.
 */
function floatCopy(geometry: THREE.BufferGeometry): THREE.BufferGeometry {
  const index = geometry.index;
  const count = index ? index.count : geometry.getAttribute('position').count;
  const copy = new THREE.BufferGeometry();
  for (const [name, attribute] of Object.entries(geometry.attributes)) {
    const size = attribute.itemSize;
    const values = new Float32Array(count * size);
    for (let k = 0; k < count; k++) {
      const v = index ? index.getX(k) : k;
      for (let c = 0; c < size; c++) values[k * size + c] = attribute.getComponent(v, c);
    }
    copy.setAttribute(name, new THREE.BufferAttribute(values, size));
  }
  return copy;
}

/** The room `p` lies in, or else the one nearest it. */
function ownerOf(p: Vec2, rooms: Room[]): Room {
  const inside = roomAt(p, rooms);
  if (inside) return inside;
  let best = rooms[0];
  let bestDistance = Infinity;
  for (const room of rooms) {
    const poly = room.floorPolygon;
    for (let i = 0; i < poly.length; i++) {
      const d = distancePointToSegment(p, poly[i], poly[(i + 1) % poly.length]);
      if (d < bestDistance) [best, bestDistance] = [room, d];
    }
  }
  return best;
}

/**
 * A convex polygon cut in two by the line through segment a–b, or null when
 * the line misses it or, unless `line`, the segment does. The line is
 * followed beyond the segment's ends, which cuts more than it must but never
 * wrongly.
 */
function cut(poly: Vec2[], a: Vec2, b: Vec2, line = false): [Vec2[], Vec2[]] | null {
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (length < 1e-9) return null;
  const side = poly.map((p) => cross(a, b, p) / length);
  if (side.every((s) => s > -1e-9) || side.every((s) => s < 1e-9)) return null;
  // Along the segment, the piece spans [lo, hi]; no overlap with [0, 1], no cut.
  const along = poly.map((p) => ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / length ** 2);
  if (!line && (Math.max(...along) < 1e-9 || Math.min(...along) > 1 - 1e-9)) return null;
  const left: Vec2[] = [];
  const right: Vec2[] = [];
  for (let i = 0; i < poly.length; i++) {
    const j = (i + 1) % poly.length;
    const [p, q, sp, sq] = [poly[i], poly[j], side[i], side[j]];
    if (sp >= -1e-9) left.push(p);
    if (sp <= 1e-9) right.push(p);
    if ((sp > 1e-9 && sq < -1e-9) || (sp < -1e-9 && sq > 1e-9)) {
      const t = sp / (sp - sq);
      const x: Vec2 = [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
      left.push(x);
      right.push(x);
    }
  }
  return [left, right];
}

/**
 * The geometry's triangles shared out among the rooms, one geometry per room
 * that gets any, its attributes (texture coordinates, light-map coordinates,
 * normals) carried over by interpolation. Seen from above the floor plane is
 * x–z; a triangle standing on edge (a slab's side) goes whole to the room
 * nearest its middle, or is left out with `sides: false`.
 */
export function splitByRooms(
  geometry: THREE.BufferGeometry,
  rooms: Room[],
  { sides = true }: { sides?: boolean } = {},
): Map<string, THREE.BufferGeometry> {
  const out = new Map<string, Map<string, number[]>>();
  const usable = rooms.filter((r) => r.floorPolygon.length >= 3);
  if (!usable.length) return new Map();
  const source = floatCopy(geometry);
  const attributes = Object.entries(source.attributes) as Array<[string, THREE.BufferAttribute]>;
  const position = source.getAttribute('position');
  const edges = usable.flatMap((r) => r.floorPolygon.map((p, i, poly): [Vec2, Vec2] => [p, poly[(i + 1) % poly.length]]));

  /** Corner v of the source, weighted, added into a room's vertex data. */
  const emit = (roomId: string, base: number, weights: [number, number, number]) => {
    let data = out.get(roomId);
    if (!data) out.set(roomId, (data = new Map(attributes.map(([name]) => [name, []]))));
    for (const [name, attribute] of attributes) {
      const values = data.get(name)!;
      for (let c = 0; c < attribute.itemSize; c++) {
        let v = 0;
        for (let k = 0; k < 3; k++) v += weights[k] * attribute.getComponent(base + k, c);
        values.push(v);
      }
    }
  };

  for (let base = 0; base < position.count; base += 3) {
    const corners = [0, 1, 2].map((k): Vec2 => [position.getX(base + k), position.getZ(base + k)]);
    const [pa, pb, pc] = corners;
    const area = cross(pa, pb, pc);
    if (Math.abs(area) < FLAT) {
      if (!sides) continue;
      const middle: Vec2 = [(pa[0] + pb[0] + pc[0]) / 3, (pa[1] + pb[1] + pc[1]) / 3];
      const roomId = ownerOf(middle, usable).id;
      for (let k = 0; k < 3; k++) emit(roomId, base, [k === 0 ? 1 : 0, k === 1 ? 1 : 0, k === 2 ? 1 : 0]);
      continue;
    }
    const cutAll = (from: Vec2[][], line: boolean) =>
      edges.reduce((pieces, [a, b]) => pieces.flatMap((piece) => cut(piece, a, b, line) ?? [piece]), from);
    // A piece in no room (a margin under the walls, a gap between outlines)
    // goes to the nearest: cut along the whole lines first, so that a strip
    // along two rooms is not given to one of them whole.
    const pieces = cutAll([corners], false).flatMap((piece) =>
      roomAt(middleOf(piece), usable) ? [piece] : cutAll([piece], true),
    );
    const weigh = (p: Vec2): [number, number, number] => {
      const wb = cross(pa, p, pc) / area;
      const wc = cross(pa, pb, p) / area;
      return [1 - wb - wc, wb, wc];
    };
    for (const piece of pieces) {
      let twice = 0;
      for (let i = 1; i + 1 < piece.length; i++) twice += cross(piece[0], piece[i], piece[i + 1]);
      if (Math.abs(twice) < FLAT) continue;
      const roomId = ownerOf(middleOf(piece), usable).id;
      // A fan, in the triangle's own winding, which cutting kept.
      for (let i = 1; i + 1 < piece.length; i++) {
        for (const p of [piece[0], piece[i], piece[i + 1]]) emit(roomId, base, weigh(p));
      }
    }
  }
  source.dispose();

  const result = new Map<string, THREE.BufferGeometry>();
  for (const [roomId, data] of out) {
    const g = new THREE.BufferGeometry();
    for (const [name, attribute] of attributes) {
      g.setAttribute(name, new THREE.Float32BufferAttribute(data.get(name)!, attribute.itemSize));
    }
    result.set(roomId, g);
  }
  return result;
}

/**
 * The metres one tile of a floor's texture spans along x and z, read off its
 * texture coordinates, which the rebuild lays flat in tiles (`floor_uvs`).
 */
export function tileOf(geometry: THREE.BufferGeometry): [number, number] | undefined {
  const position = geometry.getAttribute('position');
  const uv = geometry.getAttribute('uv');
  if (!uv) return undefined;
  const span = (get: (i: number) => number) => {
    let [lo, hi] = [Infinity, -Infinity];
    for (let i = 0; i < position.count; i++) [lo, hi] = [Math.min(lo, get(i)), Math.max(hi, get(i))];
    return hi - lo;
  };
  const [u, v] = [span((i) => uv.getX(i)), span((i) => uv.getY(i))];
  if (u < 1e-6 || v < 1e-6) return undefined;
  return [span((i) => position.getX(i)) / u, span((i) => position.getZ(i)) / v];
}

/**
 * Give every room its own part of the model's floors. A floor baked for a
 * room the manifest no longer has (an open plan cut into rooms afterwards),
 * and a zone laid on top of it (`floorzone-*`, the tiled part of a hall), are
 * replaced by one mesh per room, tagged with its `roomId` and the size of
 * its texture's tile (`tileM`), so that a new floor is laid on all of a
 * room's pieces at one scale. Run before the materials are set up: each
 * piece gets a copy of its floor's material, so that each can change on its
 * own.
 */
export function splitFloors(model: THREE.Object3D, rooms: Room[]): void {
  const known = new Set(rooms.map((r) => r.id));
  const whole: THREE.Mesh[] = [];
  model.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const roomId = node.userData.roomId;
    if ((typeof roomId === 'string' && !known.has(roomId)) || node.name.startsWith('floorzone-')) {
      whole.push(node);
    }
  });
  model.updateMatrixWorld(true);
  for (const mesh of whole) {
    const parent = mesh.parent;
    if (!parent) continue;
    // Cut where the rooms are, in the model's space, then put back in the parent's.
    const placed = floatCopy(mesh.geometry).applyMatrix4(mesh.matrixWorld);
    // A zone lies on the floor like a sheet: its 2 mm edge drew a line
    // across a new floor laid over both.
    const zone = mesh.name.startsWith('floorzone-');
    const pieces = splitByRooms(placed, rooms, { sides: !zone });
    const tileM = mesh.userData.tileM ?? tileOf(placed);
    placed.dispose();
    if (!pieces.size) continue;
    const back = parent.matrixWorld.clone().invert();
    const material = mesh.material as THREE.Material;
    for (const [roomId, geometry] of pieces) {
      const piece = new THREE.Mesh(geometry.applyMatrix4(back), material.clone());
      piece.name = mesh.name;
      piece.userData = { ...mesh.userData, roomId, tileM };
      parent.add(piece);
    }
    parent.remove(mesh);
    mesh.geometry.dispose();
    material.dispose();
  }
}
