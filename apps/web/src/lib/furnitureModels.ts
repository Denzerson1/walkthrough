/**
 * Loading catalog furniture: the prepared models (scripts/prepare-models.mjs)
 * have meshopt-compressed geometry and KTX2 textures, so the loader needs
 * both decoders; a phone gets the lighter file.
 */

import type * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import type { CatalogItem } from './store';

/** A phone: the lighter model, smaller textures, a lower pixel ratio. */
export const IS_PHONE =
  typeof window !== 'undefined' &&
  window.matchMedia('(pointer: coarse) and (max-width: 1023px), (pointer: coarse) and (max-height: 600px)').matches;

/** The model file a piece is drawn from on this device. */
export function modelUrl(item: Pick<CatalogItem, 'glb' | 'glbPhone'>): string | null {
  return (IS_PHONE ? item.glbPhone : null) ?? item.glb ?? null;
}

let ktx2: KTX2Loader | null = null;

/**
 * A glTF loader for furniture models, decoding on worker threads. KTX2
 * needs a renderer to pick the GPU's compressed format; one transcoder is
 * shared across renderers, as the formats are the same on one device.
 */
export function furnitureLoader(renderer: THREE.WebGLRenderer): GLTFLoader {
  if (!ktx2) {
    ktx2 = new KTX2Loader()
      .setTranscoderPath(`${import.meta.env.BASE_URL}basis/`)
      .setWorkerLimit(2)
      .detectSupport(renderer);
  }
  return new GLTFLoader().setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
}
