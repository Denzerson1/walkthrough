import type * as THREE from 'three';

/**
 * Which floor splats draw: a room's come off while it shows a replacement
 * (not while comparing with the original). "*" is one file for every room,
 * off as soon as any room is replaced. Added and removed rather than hidden:
 * SparkRenderer collects splat meshes itself, so leaving the scene graph is
 * the reliable way to stop one drawing.
 */
export function syncFloorSplats(
  scene: THREE.Object3D,
  floorSplats: Map<string, THREE.Object3D> | undefined,
  floors: Record<string, string>,
  showingOriginal: boolean,
): void {
  if (!floorSplats) return;
  const anyReplaced = Object.keys(floors).length > 0;
  for (const [roomId, mesh] of floorSplats) {
    const replaced = roomId === '*' ? anyReplaced : roomId in floors;
    const show = showingOriginal || !replaced;
    if (show && mesh.parent !== scene) scene.add(mesh);
    else if (!show && mesh.parent === scene) scene.remove(mesh);
  }
}
