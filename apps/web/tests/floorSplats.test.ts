import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { syncFloorSplats } from '../src/lib/floorSplats';

function flat(rooms: string[]) {
  const scene = new THREE.Scene();
  const splats = new Map(rooms.map((id) => [id, new THREE.Object3D()] as const));
  for (const mesh of splats.values()) scene.add(mesh);
  return { scene, splats };
}

const shown = (scene: THREE.Object3D, splats: Map<string, THREE.Object3D>) =>
  [...splats].filter(([, m]) => m.parent === scene).map(([id]) => id);

describe('floor splats per room', () => {
  it('replacing one room hides only that room’s captured floor', () => {
    const { scene, splats } = flat(['hall', 'kitchen', 'bedroom']);
    syncFloorSplats(scene, splats, { kitchen: 'oak-plank-natural' }, false);
    expect(shown(scene, splats)).toEqual(['hall', 'bedroom']);
  });

  it('comparing with the original brings every floor back', () => {
    const { scene, splats } = flat(['hall', 'kitchen']);
    syncFloorSplats(scene, splats, { kitchen: 'oak-plank-natural' }, false);
    syncFloorSplats(scene, splats, { kitchen: 'oak-plank-natural' }, true);
    expect(shown(scene, splats)).toEqual(['hall', 'kitchen']);
  });

  it('resetting the floors restores them', () => {
    const { scene, splats } = flat(['hall', 'kitchen']);
    syncFloorSplats(scene, splats, { hall: 'x', kitchen: 'y' }, false);
    expect(shown(scene, splats)).toEqual([]);
    syncFloorSplats(scene, splats, {}, false);
    expect(shown(scene, splats)).toEqual(['hall', 'kitchen']);
  });

  it('an older export’s single floor file goes as soon as any room is replaced', () => {
    const { scene, splats } = flat(['*']);
    syncFloorSplats(scene, splats, {}, false);
    expect(shown(scene, splats)).toEqual(['*']);
    syncFloorSplats(scene, splats, { hall: 'x' }, false);
    expect(shown(scene, splats)).toEqual([]);
  });
});
