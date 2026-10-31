/**
 * The furniture people place or a style kit stands in the flat, in the 3D
 * scene: one group per placed piece, kept across changes so that moving a
 * piece moves it, a new piece fades in and a removed one fades out (a swap
 * is both at once), and nothing else is rebuilt.
 *
 * Models are cached by URL and shared by every placement; the cache is kept
 * within a memory budget, and a model no piece shows any more is disposed,
 * textures and all, once the budget needs the room.
 */

import * as THREE from 'three';
import { apiUrl } from './api';
import { furnitureLoader, IS_PHONE, modelUrl } from './furnitureModels';
import type { Room, Slot } from './manifest';
import type { CatalogItem } from './store';
import { baseFor } from './styles';
import { CASTS } from './sun';
import type { PlacedItem } from './viewerState';

/**
 * Footprint used when a catalog item has no dimensions. Shared with the
 * server solver (routes_layout.py) so a validated layout and the drawn boxes agree.
 */
export const DEFAULT_ITEM_SIZE: [number, number, number] = [0.6, 0.6, 0.6];
/** How long a piece takes to fade in or out, ms. */
const FADE_MS = 260;
/** GPU memory the cached models may take before unused ones are dropped. */
const BUDGET_BYTES = (IS_PHONE ? 120 : 400) * 1024 * 1024;
const LAMPS = new Set(['floor_lamp', 'table_lamp', 'pendant_lamp']);

interface CachedModel {
  scene: THREE.Group;
  bytes: number;
  users: number;
  lastUsed: number;
}

const models = new Map<string, Promise<CachedModel>>();
const loaded = new Map<string, CachedModel>();

function textureBytes(texture: THREE.Texture): number {
  const image = texture.image as { width?: number; height?: number } | undefined;
  const px = (image?.width ?? 0) * (image?.height ?? 0);
  // Compressed: about a byte a pixel; plain RGBA four. Mips add a third.
  return px * ((texture as THREE.CompressedTexture).isCompressedTexture ? 1 : 4) * 1.34;
}

function measure(scene: THREE.Object3D): number {
  let bytes = 0;
  const seen = new Set<unknown>();
  scene.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    if (!seen.has(node.geometry)) {
      seen.add(node.geometry);
      for (const attribute of Object.values(node.geometry.attributes)) {
        bytes += (attribute as THREE.BufferAttribute).array.byteLength;
      }
      bytes += node.geometry.index?.array.byteLength ?? 0;
    }
    for (const material of [node.material].flat()) {
      for (const value of Object.values(material)) {
        if (value instanceof THREE.Texture && !seen.has(value)) {
          seen.add(value);
          bytes += textureBytes(value);
        }
      }
    }
  });
  return bytes;
}

/** Free a cached model's geometry, materials and textures. */
function disposeModel(scene: THREE.Object3D): void {
  scene.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    node.geometry.dispose();
    for (const material of [node.material].flat()) {
      for (const value of Object.values(material)) {
        if (value instanceof THREE.Texture) value.dispose();
      }
      material.dispose();
    }
  });
}

/** Drop unused models, least recently used first, until the cache fits its budget. */
function evict(): void {
  let total = [...loaded.values()].reduce((sum, m) => sum + m.bytes, 0);
  const idle = [...loaded.entries()]
    .filter(([, m]) => m.users === 0)
    .sort(([, a], [, b]) => a.lastUsed - b.lastUsed);
  for (const [url, model] of idle) {
    if (total <= BUDGET_BYTES) break;
    disposeModel(model.scene);
    loaded.delete(url);
    models.delete(url);
    total -= model.bytes;
  }
}

function acquire(url: string, renderer: THREE.WebGLRenderer): Promise<CachedModel> {
  let pending = models.get(url);
  if (!pending) {
    pending = furnitureLoader(renderer)
      .loadAsync(apiUrl(url))
      .then((gltf) => {
        const model = { scene: gltf.scene, bytes: measure(gltf.scene), users: 0, lastUsed: 0 };
        loaded.set(url, model);
        return model;
      });
    // A failed fetch must not poison the cache: the next placement retries.
    pending.catch(() => models.delete(url));
    models.set(url, pending);
  }
  return pending.then((model) => {
    model.users += 1;
    model.lastUsed = performance.now();
    return model;
  });
}

function release(url: string): void {
  const model = loaded.get(url);
  if (!model) return;
  model.users = Math.max(0, model.users - 1);
  model.lastUsed = performance.now();
  // Shown nowhere: off the GPU now (three uploads it again if it comes back),
  // kept parsed for a quick return while the budget allows.
  if (model.users === 0) disposeModel(model.scene);
  evict();
}

/** Bytes the cached models take, for the memory check. */
export function cachedModelBytes(): number {
  return [...loaded.values()].reduce((sum, m) => sum + m.bytes, 0);
}

// ---------------------------------------------------------------------------

/** A soft dark patch under a piece, so it stands on the floor rather than over it. */
let contactTexture: THREE.CanvasTexture | null = null;
function contactShadow(w: number, d: number): THREE.Mesh {
  if (!contactTexture) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 64;
    const g = canvas.getContext('2d')!;
    const gradient = g.createRadialGradient(32, 32, 4, 32, 32, 32);
    gradient.addColorStop(0, 'rgba(0,0,0,0.55)');
    gradient.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = gradient;
    g.fillRect(0, 0, 64, 64);
    contactTexture = new THREE.CanvasTexture(canvas);
  }
  const material = new THREE.MeshBasicMaterial({
    map: contactTexture,
    transparent: true,
    depthWrite: false,
    opacity: 0.6,
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w * 1.15, d * 1.15), material);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.004;
  mesh.renderOrder = 1;
  mesh.userData.owned = true;
  return mesh;
}

/** The box shown while a model loads: faint, the piece's size, where it will stand. */
function placeholder([w, h, d]: [number, number, number]): THREE.Mesh {
  const material = new THREE.MeshBasicMaterial({
    color: 0xd9d2c5,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  mesh.position.y = h / 2;
  mesh.userData.owned = true;
  return mesh;
}

/** A ring on the floor marking the piece the person has tapped. */
function selectionRing([w, , d]: [number, number, number]): THREE.Mesh {
  const radius = Math.max(w, d) / 2 + 0.06;
  const geometry = new THREE.RingGeometry(radius, radius + 0.045, 48);
  geometry.rotateX(-Math.PI / 2);
  const material = new THREE.MeshBasicMaterial({
    color: 0x46513f,
    transparent: true,
    opacity: 0.95,
    depthTest: false,
  });
  const ring = new THREE.Mesh(geometry, material);
  ring.position.y = 0.012;
  ring.renderOrder = 3;
  ring.userData.owned = true;
  ring.userData.selection = true;
  return ring;
}

/**
 * Scale `model` uniformly to fit `size` and stand it on y = 0, centred in
 * plan. Prepared models are already the product's size and pivot; this keeps
 * a piece inside the box the slot was checked against either way.
 */
function fitToBox(model: THREE.Object3D, size: [number, number, number]): void {
  const extent = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3());
  if (extent.x < 1e-6 || extent.y < 1e-6 || extent.z < 1e-6) return;
  model.scale.setScalar(Math.min(size[0] / extent.x, size[1] / extent.y, size[2] / extent.z));
  const bounds = new THREE.Box3().setFromObject(model);
  const centre = bounds.getCenter(new THREE.Vector3());
  model.position.x -= centre.x;
  model.position.z -= centre.z;
  model.position.y -= bounds.min.y;
}

interface Shown {
  group: THREE.Group;
  url: string | null;
  itemId: string;
  colorHex?: string;
  /** Per-placement material copies: their opacity fades, their colour tints. */
  materials: THREE.Material[];
  /** The light-coloured, unmetallic materials of a lamp: its shade, which glows. */
  shades: THREE.MeshStandardMaterial[];
  removed: boolean;
}

interface Fade {
  shown: Shown;
  from: number;
  to: number;
  start: number;
}

export class FurnitureLayer {
  readonly group = new THREE.Group();
  private shown = new Map<string, Shown>();
  private fades: Fade[] = [];
  private glow = 0;
  private reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /**
   * `onChange`: what casts a shadow changed (a model arrived, a piece moved
   * or went), so the sun's shadow map is drawn again.
   */
  constructor(
    private renderer: THREE.WebGLRenderer,
    private onChange: () => void = () => undefined,
  ) {}

  /** Bring the scene in line with the placed items. */
  sync(
    items: PlacedItem[],
    catalog: CatalogItem[],
    rooms: Room[],
    selectedUid: string | null,
  ): void {
    const wanted = new Set(items.map((i) => i.uid));
    for (const [uid, shown] of this.shown) {
      if (!wanted.has(uid)) this.remove(uid, shown);
    }
    let moved = false;
    for (const item of items) {
      const entry = catalog.find((f) => f.id === item.itemId);
      const size = (entry?.dimensionsM ?? DEFAULT_ITEM_SIZE) as [number, number, number];
      let shown = this.shown.get(item.uid);
      if (shown && (shown.itemId !== item.itemId || shown.colorHex !== item.colorHex)) {
        this.remove(item.uid, shown);
        shown = undefined;
      }
      if (!shown) shown = this.add(item, entry, size);
      const { group } = shown;
      const before = group.position.clone();
      const turned = group.rotation.y;
      group.position.set(item.position[0], standingHeight(item, items, catalog, rooms), item.position[2]);
      // Negated: geometry.ts and layout.py turn (x, z) counter-clockwise, a
      // three.js rotation about +Y the other way.
      group.rotation.y = (-item.yaw * Math.PI) / 180;
      if (!before.equals(group.position) || turned !== group.rotation.y) moved = true;
      const ring = group.children.find((c) => c.userData.selection);
      if (item.uid === selectedUid && !ring) group.add(selectionRing(size));
      if (item.uid !== selectedUid && ring) {
        group.remove(ring);
        disposeOwned(ring);
      }
    }
    if (moved) this.onChange();
  }

  /** How much a lamp's shade glows, 0 by day to 1 at night. */
  setGlow(amount: number): void {
    this.glow = amount;
    for (const shown of this.shown.values()) for (const m of shown.shades) applyGlow(m, amount);
  }

  /** Advance the fades; true while one is running, so the scene keeps drawing. */
  tick(now: number): boolean {
    this.fades = this.fades.filter((fade) => {
      const t = Math.min((now - fade.start) / FADE_MS, 1);
      setOpacity(fade.shown, fade.from + (fade.to - fade.from) * t);
      if (t < 1) return true;
      if (fade.shown.removed) this.drop(fade.shown);
      else setOpacity(fade.shown, 1);
      return false;
    });
    return this.fades.length > 0;
  }

  /** Take every piece out of the scene and let go of its model. */
  dispose(): void {
    for (const shown of [...this.shown.values(), ...this.fades.map((f) => f.shown)]) {
      this.drop(shown);
    }
    this.shown.clear();
    this.fades = [];
  }

  private drop(shown: Shown): void {
    const casting = shown.materials.length > 0;
    this.group.remove(shown.group);
    disposeOwned(shown.group);
    for (const m of shown.materials) m.dispose();
    if (shown.url && shown.materials.length) release(shown.url);
    if (casting) this.onChange();
  }

  private add(item: PlacedItem, entry: CatalogItem | undefined, size: [number, number, number]): Shown {
    const group = new THREE.Group();
    group.userData.uid = item.uid;
    group.userData.roomId = item.roomId;
    group.userData.size = size;
    group.add(contactShadow(size[0], size[2]));
    const box = placeholder(size);
    group.add(box);
    this.group.add(group);
    const url = entry ? modelUrl(entry) : null;
    const shown: Shown = {
      group, url, itemId: item.itemId, colorHex: item.colorHex, materials: [], shades: [], removed: false,
    };
    this.shown.set(item.uid, shown);
    if (!url) return shown;
    void acquire(url, this.renderer)
      .then((cached) => {
        if (shown.removed) {
          release(url);
          return;
        }
        const model = cached.scene.clone(true);
        fitToBox(model, size);
        const tint = item.colorHex ? new THREE.Color(item.colorHex) : null;
        const lamp = LAMPS.has(entry?.category ?? '');
        model.traverse((node) => {
          if (!(node instanceof THREE.Mesh)) return;
          node.material = [node.material].flat().map((m: THREE.Material) => {
            const copy = m.clone() as THREE.MeshStandardMaterial;
            if (tint) copy.color = tint;
            if (lamp && isShade(copy)) shown.shades.push(copy);
            shown.materials.push(copy);
            return copy;
          });
          if (node.material.length === 1) node.material = node.material[0];
          // It shades the floor and walls in the sun, as the flat's own pieces do.
          node.layers.enable(CASTS);
        });
        for (const shade of shown.shades) applyGlow(shade, this.glow);
        group.remove(box);
        disposeOwned(box);
        group.add(model);
        this.fade(shown, 0, 1);
        this.onChange();
      })
      .catch(() => {
        // Keep the faint box: a missing model shows as the space it takes.
      });
    return shown;
  }

  private remove(uid: string, shown: Shown): void {
    this.shown.delete(uid);
    shown.removed = true;
    shown.group.userData.uid = undefined; // no longer pickable
    if (!shown.materials.length) this.drop(shown);
    else this.fade(shown, 1, 0);
  }

  private fade(shown: Shown, from: number, to: number): void {
    if (this.reducedMotion) {
      if (shown.removed) this.drop(shown);
      return;
    }
    setOpacity(shown, from);
    this.fades.push({ shown, from, to, start: performance.now() });
  }
}

/**
 * How high a placed piece stands: on the floor, or on the piece in the slot
 * it stands on (a lamp on a nightstand). Worked out here rather than kept in
 * the state, so a shared link, which carries plan positions, stands it right.
 */
function standingHeight(
  item: PlacedItem,
  items: PlacedItem[],
  catalog: CatalogItem[],
  rooms: Room[],
): number {
  if (!item.slotId) return 0;
  const slots = rooms.find((r) => r.id === item.roomId)?.slots ?? [];
  const slot = slots.find((s) => s.id === item.slotId);
  if (!slot?.on) return 0;
  const placed = items.flatMap((i) => {
    const s = i.roomId === item.roomId ? slots.find((x: Slot) => x.id === i.slotId) : undefined;
    const piece = catalog.find((f) => f.id === i.itemId);
    return s && piece ? [{ slot: s, item: piece }] : [];
  });
  return baseFor(slot, placed) ?? 0;
}

function setOpacity(shown: Shown, opacity: number): void {
  for (const m of shown.materials) {
    m.transparent = opacity < 1;
    m.opacity = opacity;
    m.depthWrite = opacity >= 1;
  }
}

/** A lamp's shade or globe: light-coloured and not metal. */
function isShade(m: THREE.MeshStandardMaterial): boolean {
  if (!m.color || m.metalness > 0.4) return false;
  const { r, g, b } = m.color;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 || Boolean(m.map && m.name.match(/shade|glass|globe/i));
}

function applyGlow(m: THREE.MeshStandardMaterial, amount: number): void {
  m.emissive = new THREE.Color(0xffd9a8);
  m.emissiveMap = m.map;
  m.emissiveIntensity = 0.9 * amount;
}

/** Free what this file created for a piece (its shadow, box, ring), not the shared model. */
function disposeOwned(root: THREE.Object3D): void {
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh) || !node.userData.owned) return;
    node.geometry.dispose();
    for (const m of [node.material].flat()) m.dispose();
  });
}
