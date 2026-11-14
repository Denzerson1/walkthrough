import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { mirrorGlass } from '../src/lib/mirror';

/**
 * A 0.4 x 1.4 m mirror on a wall at z = 0.96, facing -z, stored the way a
 * published model stores it (KHR_mesh_quantization): positions as
 * normalized 16-bit integers in -1..1, put in place by the node's matrix.
 */
function quantizedMirror(): { geometry: THREE.BufferGeometry; matrixWorld: THREE.Matrix4; corners: number[][] } {
  const corners = [
    [-4.7, 0.34, 0.96],
    [-4.3, 0.34, 0.96],
    [-4.3, 1.78, 0.96],
    [-4.7, 1.78, 0.96],
  ];
  const offset = new THREE.Vector3(-4.5, 1.06, 0.96);
  const scale = 0.72;
  const positions = new Int16Array(12);
  const normals = new Int16Array(12);
  corners.forEach((c, i) => {
    positions[i * 3] = Math.round(((c[0] - offset.x) / scale) * 32767);
    positions[i * 3 + 1] = Math.round(((c[1] - offset.y) / scale) * 32767);
    positions[i * 3 + 2] = Math.round(((c[2] - offset.z) / scale) * 32767);
    normals[i * 3 + 2] = -32767;
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3, true));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3, true));
  geometry.setIndex([0, 2, 1, 0, 3, 2]);
  const matrixWorld = new THREE.Matrix4().compose(offset, new THREE.Quaternion(), new THREE.Vector3(scale, scale, scale));
  return { geometry, matrixWorld, corners };
}

describe('mirrorGlass', () => {
  it('keeps a quantized mirror on its wall', () => {
    const { geometry, matrixWorld, corners } = quantizedMirror();
    const glass = mirrorGlass(geometry, matrixWorld);
    expect(glass).not.toBeNull();
    const position = glass!.geometry.getAttribute('position');
    const box = new THREE.Box3();
    const p = new THREE.Vector3();
    for (let i = 0; i < position.count; i++) {
      box.expandByPoint(p.fromBufferAttribute(position, i).applyMatrix4(glass!.frame));
    }
    // In place it would wrap: the copy drawn metres away, across the hall.
    expect(box.min.x).toBeCloseTo(corners[0][0], 3);
    expect(box.max.x).toBeCloseTo(corners[1][0], 3);
    expect(box.min.y).toBeCloseTo(corners[0][1], 3);
    expect(box.max.y).toBeCloseTo(corners[2][1], 3);
    expect(box.min.z).toBeCloseTo(0.96, 3);
    expect(box.max.z).toBeCloseTo(0.96, 3);
  });

  it('lays the glass in its local xy plane, looking along +z', () => {
    const { geometry, matrixWorld } = quantizedMirror();
    const glass = mirrorGlass(geometry, matrixWorld)!;
    const position = glass.geometry.getAttribute('position');
    for (let i = 0; i < position.count; i++) expect(position.getZ(i)).toBeCloseTo(0, 4);
    const looks = new THREE.Vector3(0, 0, 1).transformDirection(glass.frame);
    expect(looks.z).toBeCloseTo(-1, 5);
  });

  it('leaves the source untouched', () => {
    const { geometry, matrixWorld } = quantizedMirror();
    const before = Array.from(geometry.getAttribute('position').array);
    mirrorGlass(geometry, matrixWorld);
    expect(Array.from(geometry.getAttribute('position').array)).toEqual(before);
  });

  it('skips a face whose normals cancel', () => {
    const { geometry, matrixWorld } = quantizedMirror();
    const normal = geometry.getAttribute('normal') as THREE.BufferAttribute;
    normal.setXYZ(0, 0, 0, 1);
    normal.setXYZ(1, 0, 0, 1);
    expect(mirrorGlass(geometry, matrixWorld)).toBeNull();
  });
});
