/**
 * Thumbnails for catalog furniture, rendered from the real mesh.
 *
 * The catalog ships no thumbnail images — the meshes are downloaded at setup
 * (`pnpm seed:assets`) and committing a picture of each one would duplicate
 * them in git for no gain. So the picker renders each item once, in the
 * browser, and caches the result for the session.
 *
 * One small renderer is shared by every thumbnail and torn down when the
 * queue empties: browsers cap concurrent WebGL contexts at around 16, and the
 * viewer itself needs one of them.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { apiUrl } from './api';

const SIZE = 192;

const cache = new Map<string, Promise<string>>();

let renderer: THREE.WebGLRenderer | null = null;
let outstanding = 0;

function acquireRenderer(): THREE.WebGLRenderer {
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(1);
    renderer.setSize(SIZE, SIZE);
  }
  outstanding += 1;
  return renderer;
}

function releaseRenderer(): void {
  outstanding -= 1;
  if (outstanding > 0 || !renderer) return;
  renderer.dispose();
  renderer.forceContextLoss();
  renderer = null;
}

/**
 * A three-quarter view of `glb`, tinted to `colorHex`, as a PNG data URL.
 *
 * Rejects rather than resolving to a blank image, so the caller can fall back
 * to the colour swatch instead of showing an empty tile.
 */
export function furnitureThumbnail(
  id: string,
  glb: string,
  colorHex: string | undefined,
): Promise<string> {
  const existing = cache.get(id);
  if (existing) return existing;

  const pending = render(glb, colorHex);
  // A failure must not be cached, or the tile can never recover.
  pending.catch(() => cache.delete(id));
  cache.set(id, pending);
  return pending;
}

async function render(glb: string, colorHex: string | undefined): Promise<string> {
  const gltf = await new GLTFLoader().loadAsync(apiUrl(glb));
  const model = gltf.scene;

  if (colorHex) {
    const color = new THREE.Color(colorHex);
    model.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) return;
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      child.material = materials.map((m) => {
        const clone = (m as THREE.MeshStandardMaterial).clone();
        clone.color = color;
        return clone;
      });
      if (Array.isArray(child.material) && child.material.length === 1) {
        child.material = child.material[0];
      }
    });
  }

  // Normalise: centre on the origin and scale the largest dimension to 1, so
  // a wardrobe and a lamp both fill their tile.
  const bounds = new THREE.Box3().setFromObject(model);
  const size = bounds.getSize(new THREE.Vector3());
  const centre = bounds.getCenter(new THREE.Vector3());
  const longest = Math.max(size.x, size.y, size.z) || 1;
  model.position.sub(centre);
  model.scale.setScalar(1 / longest);

  const scene = new THREE.Scene();
  scene.add(model);
  scene.add(new THREE.AmbientLight(0xffffff, 2.1));
  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(2, 3, 2.5);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.8);
  fill.position.set(-2, 1, -1.5);
  scene.add(fill);

  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
  camera.position.set(1.5, 1.15, 1.9);
  camera.lookAt(0, -0.05, 0);

  const gl = acquireRenderer();
  try {
    gl.render(scene, camera);
    return gl.domElement.toDataURL('image/png');
  } finally {
    // The model's geometry and materials came from this one load and are not
    // shared with the viewer's cache, so they are ours to free.
    model.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) return;
      child.geometry.dispose();
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) material.dispose();
    });
    releaseRenderer();
  }
}
