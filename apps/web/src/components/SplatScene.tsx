/**
 * The 3D scene: Spark splats plus three.js meshes for replacement floors
 * and placed furniture.
 *
 * Spark renders Gaussian splats inside a normal three.js scene, so the
 * replacement floor mesh and furniture are correctly occluded by splats
 * (brief M3) without any extra work — they share one depth buffer.
 */

import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import {
  DEFAULT_FOV,
  TRANSITION_MS,
  applyZoom,
  clampPitch,
  dragSensitivity,
  easeInOutCubic,
  lerpAngle,
  lerpVec3,
  lookDirection,
} from '../lib/camera';
import type { Manifest, Room } from '../lib/manifest';
import { FLOOR_DEFAULT_COLOR, floorColor } from '../lib/floorColors';
import type { CatalogItem } from '../lib/store';
import type { ViewerState } from '../lib/viewerState';

/**
 * Footprint used when a catalog item has no dimensions. Shared with the
 * server solver so a validated layout and the drawn boxes agree.
 */
export const DEFAULT_ITEM_SIZE: [number, number, number] = [0.6, 0.6, 0.6];

interface Props {
  manifest: Manifest;
  sceneUrl: string;
  /** Floor splats as a separate mesh, when the pipeline has split the scene. */
  floorUrl?: string;
  activeRoomId: string | null;
  viewer: ViewerState;
  floorCatalog: CatalogItem[];
  furnitureCatalog: CatalogItem[];
  showingOriginal: boolean;
  onProgress?: (fraction: number) => void;
  onReady: () => void;
  onError: (message: string) => void;
  onFps?: (fps: number) => void;
}

interface CameraTarget {
  position: [number, number, number];
  yaw: number;
}

export function SplatScene({
  manifest,
  sceneUrl,
  floorUrl,
  activeRoomId,
  viewer,
  floorCatalog,
  furnitureCatalog,
  showingOriginal,
  onProgress,
  onReady,
  onError,
  onFps,
}: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({
    yaw: 0,
    pitch: 0,
    fov: DEFAULT_FOV,
    position: [0, 1.6, 0] as [number, number, number],
    transition: null as null | {
      from: CameraTarget;
      to: CameraTarget;
      startedAt: number;
    },
  });
  const sceneRef = useRef<{
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    floorGroup: THREE.Group;
    furnitureGroup: THREE.Group;
    splat?: SplatMesh;
    floorSplat?: SplatMesh;
  } | null>(null);

  // ---- one-time scene setup -------------------------------------------
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    if (!isWebGL2Available()) {
      onError(
        'This browser cannot show 3D walkthroughs. Try the latest Safari, Chrome or Firefox.',
      );
      return;
    }

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      DEFAULT_FOV,
      mount.clientWidth / mount.clientHeight,
      0.05,
      200,
    );

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: false,
        alpha: false,
        powerPreference: 'high-performance',
      });
    } catch {
      onError('Could not start WebGL on this device.');
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x14171c, 1);
    mount.appendChild(renderer.domElement);

    const spark = new SparkRenderer({ renderer });
    scene.add(spark);

    // Ambient plus a soft key so placed furniture is not flat. The real
    // environment map from the splat arrives in M6.
    scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(2, 4, 3);
    scene.add(key);

    const floorGroup = new THREE.Group();
    const furnitureGroup = new THREE.Group();
    scene.add(floorGroup, furnitureGroup);

    sceneRef.current = { scene, camera, renderer, floorGroup, furnitureGroup };

    let disposed = false;
    // No orientation flip anywhere below. Scenes reaching the viewer are
    // already Y-up with the floor at y=0 — the pipeline's `align` stage
    // guarantees it and docs/SPEC.md §4 states it. The 180°-about-X flip that
    // Spark examples use is for raw 3DGS exports in the Y-down convention,
    // which never reach here.
    //
    // When the pipeline has split the scene we load the body and the floor as
    // two meshes, so the floor can be hidden for a replacement. Otherwise we
    // load the single combined scene and floor replacement is unavailable.
    let pending = floorUrl ? 2 : 1;
    const settle = () => {
      if (disposed) return;
      pending -= 1;
      if (pending === 0) {
        onProgress?.(1);
        onReady();
      }
    };

    const splat = new SplatMesh({ url: sceneUrl, onLoad: settle });
    splat.quaternion.identity();
    scene.add(splat);
    sceneRef.current.splat = splat;

    if (floorUrl) {
      const floorSplat = new SplatMesh({ url: floorUrl, onLoad: settle });
      floorSplat.quaternion.identity();
      scene.add(floorSplat);
      sceneRef.current.floorSplat = floorSplat;
    }

    const onResize = () => {
      if (!mount) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    window.addEventListener('resize', onResize);

    let frames = 0;
    let fpsWindowStart = performance.now();
    let raf = 0;

    const tick = () => {
      raf = requestAnimationFrame(tick);
      const st = stateRef.current;

      if (st.transition) {
        const elapsed = performance.now() - st.transition.startedAt;
        const t = easeInOutCubic(elapsed / TRANSITION_MS);
        st.position = lerpVec3(st.transition.from.position, st.transition.to.position, t);
        st.yaw = lerpAngle(st.transition.from.yaw, st.transition.to.yaw, t);
        if (elapsed >= TRANSITION_MS) st.transition = null;
      }

      camera.position.set(st.position[0], st.position[1], st.position[2]);
      const dir = lookDirection(st.yaw, st.pitch);
      camera.lookAt(
        st.position[0] + dir[0],
        st.position[1] + dir[1],
        st.position[2] + dir[2],
      );
      if (camera.fov !== st.fov) {
        camera.fov = st.fov;
        camera.updateProjectionMatrix();
      }
      renderer.render(scene, camera);

      frames += 1;
      const now = performance.now();
      if (now - fpsWindowStart >= 1000) {
        onFps?.(Math.round((frames * 1000) / (now - fpsWindowStart)));
        frames = 0;
        fpsWindowStart = now;
      }
    };
    tick();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);

      disposeChildren(floorGroup);
      disposeChildren(furnitureGroup);
      splat.dispose?.();
      sceneRef.current?.floorSplat?.dispose?.();
      scene.clear();
      renderer.dispose();
      // dispose() alone leaves the GL context alive. Browsers cap concurrent
      // contexts (~16), so without this every navigation leaks one and the
      // viewer eventually fails to start.
      renderer.forceContextLoss();

      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      sceneRef.current = null;
    };
    // sceneUrl identifies the scene; the callbacks are stable in practice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneUrl, floorUrl]);

  // ---- pointer, wheel and keyboard interaction -------------------------
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let pinchDistance = 0;

    const onPointerDown = (e: PointerEvent) => {
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      mount.setPointerCapture(e.pointerId);
    };
    const onPointerMove = (e: PointerEvent) => {
      if (!dragging) return;
      const st = stateRef.current;
      const k = dragSensitivity(st.fov);
      st.yaw -= (e.clientX - lastX) * k;
      st.pitch = clampPitch(st.pitch + (e.clientY - lastY) * k);
      lastX = e.clientX;
      lastY = e.clientY;
      // A manual look cancels an in-flight transition's rotation.
      if (st.transition) st.transition.to.yaw = st.yaw;
    };
    const onPointerUp = (e: PointerEvent) => {
      dragging = false;
      if (mount.hasPointerCapture(e.pointerId)) mount.releasePointerCapture(e.pointerId);
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      stateRef.current.fov = applyZoom(stateRef.current.fov, -e.deltaY);
    };
    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 2) return;
      e.preventDefault();
      const [a, b] = [e.touches[0], e.touches[1]];
      const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      if (pinchDistance) {
        stateRef.current.fov = applyZoom(stateRef.current.fov, (d - pinchDistance) * 8);
      }
      pinchDistance = d;
    };
    const onTouchEnd = () => {
      pinchDistance = 0;
    };
    const onKeyDown = (e: KeyboardEvent) => {
      // Let the room strip, panels and form fields have their keys first.
      const target = e.target as HTMLElement | null;
      if (
        target &&
        target !== document.body &&
        target.closest('button, input, select, textarea, [role="tablist"], [role="dialog"]')
      ) {
        return;
      }
      const st = stateRef.current;
      const step = 4;
      if (e.key === 'ArrowLeft') st.yaw += step;
      else if (e.key === 'ArrowRight') st.yaw -= step;
      else if (e.key === 'ArrowUp') st.pitch = clampPitch(st.pitch - step);
      else if (e.key === 'ArrowDown') st.pitch = clampPitch(st.pitch + step);
      else if (e.key === '+' || e.key === '=') st.fov = applyZoom(st.fov, 200);
      else if (e.key === '-') st.fov = applyZoom(st.fov, -200);
      else return;
      e.preventDefault();
    };

    mount.addEventListener('pointerdown', onPointerDown);
    mount.addEventListener('pointermove', onPointerMove);
    mount.addEventListener('pointerup', onPointerUp);
    mount.addEventListener('pointercancel', onPointerUp);
    mount.addEventListener('wheel', onWheel, { passive: false });
    mount.addEventListener('touchmove', onTouchMove, { passive: false });
    mount.addEventListener('touchend', onTouchEnd);
    window.addEventListener('keydown', onKeyDown);

    return () => {
      mount.removeEventListener('pointerdown', onPointerDown);
      mount.removeEventListener('pointermove', onPointerMove);
      mount.removeEventListener('pointerup', onPointerUp);
      mount.removeEventListener('pointercancel', onPointerUp);
      mount.removeEventListener('wheel', onWheel);
      mount.removeEventListener('touchmove', onTouchMove);
      mount.removeEventListener('touchend', onTouchEnd);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  // ---- waypoint transitions -------------------------------------------
  useEffect(() => {
    const room = manifest.rooms.find((r) => r.id === activeRoomId);
    if (!room) return;
    const st = stateRef.current;
    st.transition = {
      from: { position: st.position, yaw: st.yaw },
      to: { position: room.waypoint.position, yaw: room.waypoint.yaw },
      startedAt: performance.now(),
    };
  }, [activeRoomId, manifest]);

  // ---- replacement floors ----------------------------------------------
  //
  // The brief's M3 rule is "hide floor.spz and render the room's floor
  // polygon as a mesh". floor.spz covers the whole flat, so as soon as ANY
  // room has a replacement the original floor splats come off everywhere and
  // every room is drawn as a mesh: the replacement material where one was
  // chosen, and the room's recorded original floor colour everywhere else.
  //
  // That fallback is an approximation — a flat colour times the shading map,
  // rather than the real floor's texture. Hiding only one room's splats needs
  // per-splat masking, which is noted in docs/ROADMAP.md.
  useEffect(() => {
    const ctx = sceneRef.current;
    if (!ctx) return;
    const group = ctx.floorGroup;
    disposeChildren(group);

    const anyReplaced = Object.keys(viewer.floors).length > 0;
    const hideFloorSplats = anyReplaced;

    // Add/remove rather than toggling .visible: SparkRenderer collects splat
    // meshes itself, so taking it out of the scene graph is the reliable way
    // to stop it drawing.
    if (ctx.floorSplat) {
      if (hideFloorSplats) ctx.scene.remove(ctx.floorSplat);
      else if (ctx.floorSplat.parent !== ctx.scene) ctx.scene.add(ctx.floorSplat);
    }
    if (!hideFloorSplats) return;

    for (const room of manifest.rooms) {
      if (room.floorPolygon.length < 3) continue;
      const floorId = viewer.floors[room.id];
      const floor = floorId ? floorCatalog.find((f) => f.id === floorId) : undefined;
      group.add(buildFloorMesh(room, floor));
    }
  }, [viewer.floors, manifest, floorCatalog]);

  // "Hold to compare" must not hitch: toggle visibility rather than
  // disposing and rebuilding every floor and furniture mesh twice per press.
  useEffect(() => {
    const ctx = sceneRef.current;
    if (!ctx) return;
    ctx.floorGroup.visible = !showingOriginal;
    ctx.furnitureGroup.visible = !showingOriginal;
    if (ctx.floorSplat) {
      const show = showingOriginal || Object.keys(viewer.floors).length === 0;
      if (show && ctx.floorSplat.parent !== ctx.scene) ctx.scene.add(ctx.floorSplat);
      else if (!show) ctx.scene.remove(ctx.floorSplat);
    }
  }, [showingOriginal, viewer.floors]);

  // ---- placed furniture -------------------------------------------------
  useEffect(() => {
    const ctx = sceneRef.current;
    if (!ctx) return;
    const group = ctx.furnitureGroup;
    disposeChildren(group);

    for (const item of viewer.items) {
      const entry = furnitureCatalog.find((f) => f.id === item.itemId);
      const size = (entry?.dimensionsM ?? DEFAULT_ITEM_SIZE) as [number, number, number];
      group.add(buildPlaceholderItem(item.position, item.yaw, size, item.colorHex));
    }
  }, [viewer.items, furnitureCatalog]);

  return <div ref={mountRef} className="absolute inset-0" data-testid="splat-canvas" />;
}

// ---------------------------------------------------------------------------

function isWebGL2Available(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2'));
  } catch {
    return false;
  }
}

function disposeChildren(group: THREE.Group): void {
  for (const child of [...group.children]) {
    group.remove(child);
    if (child instanceof THREE.Mesh) {
      child.geometry.dispose();
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) {
        // material.dispose() does NOT release its textures, so clicking
        // through the floor grid would leak three GPU textures per swap.
        const standard = material as THREE.MeshStandardMaterial;
        standard.map?.dispose();
        standard.normalMap?.dispose();
        standard.roughnessMap?.dispose();
        material.dispose();
      }
    }
  }
}

/**
 * A replacement floor is the room polygon triangulated on the y=0 plane with
 * UVs in metres, so a tile of tileSizeM repeats at its true physical size.
 */
function buildFloorMesh(room: Room, floor: CatalogItem | undefined): THREE.Mesh {
  // ShapeGeometry lays the shape in the XY plane with its normal along +Z, so
  // it has to be rotated onto the floor. rotateX(-90) is the one that leaves
  // the normal pointing UP: rotateX(+90) sends it to (0,-1,0), which
  // MeshStandardMaterial back-face culls, making the new floor invisible from
  // every waypoint. That rotation also maps shape-y to +z, so the polygon is
  // built with z negated to land back in the right place.
  const shape = new THREE.Shape();
  const poly = room.floorPolygon;
  shape.moveTo(poly[0][0], -poly[0][1]);
  for (let i = 1; i < poly.length; i++) shape.lineTo(poly[i][0], -poly[i][1]);
  shape.closePath();

  const geometry = new THREE.ShapeGeometry(shape);
  geometry.rotateX(-Math.PI / 2);

  const tile = floor?.tileSizeM ?? [0.6, 0.6];
  const position = geometry.attributes.position;
  const uv = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i++) {
    uv[i * 2] = position.getX(i) / tile[0];
    uv[i * 2 + 1] = position.getZ(i) / tile[1];
  }
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));

  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(
      floor ? floorColor(floor) : (room.originalFloorColor ?? FLOOR_DEFAULT_COLOR),
    ).getHex(),
    roughness: 0.78,
    metalness: 0.0,
    // Sit a hair above y=0 so it never z-fights with floor splats.
    polygonOffset: true,
    polygonOffsetFactor: -1,
  });

  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.y = 0.001;
  mesh.renderOrder = 1;
  if (floor) loadFloorMaps(material, floor, mesh);
  return mesh;
}

function loadFloorMaps(
  material: THREE.MeshStandardMaterial,
  floor: CatalogItem,
  mesh: THREE.Mesh,
): void {
  const maps = floor.maps;
  if (!maps) return;
  // Switching floors mid-download would otherwise assign a texture to a
  // material that has already been disposed and removed from the scene.
  const stillInScene = () => mesh.parent !== null;
  const loader = new THREE.TextureLoader();
  const configure = (texture: THREE.Texture) => {
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(1, 1);
    texture.anisotropy = 8;
    return texture;
  };
  if (maps.albedo) {
    loader.load(maps.albedo, (t) => {
      if (!stillInScene()) {
        t.dispose();
        return;
      }
      t.colorSpace = THREE.SRGBColorSpace;
      material.map = configure(t);
      material.color.set(0xffffff);
      material.needsUpdate = true;
    });
  }
  if (maps.normal) {
    loader.load(maps.normal, (t) => {
      if (!stillInScene()) {
        t.dispose();
        return;
      }
      material.normalMap = configure(t);
      material.needsUpdate = true;
    });
  }
  if (maps.roughness) {
    loader.load(maps.roughness, (t) => {
      if (!stillInScene()) {
        t.dispose();
        return;
      }
      material.roughnessMap = configure(t);
      material.needsUpdate = true;
    });
  }
}



/**
 * Until GLB assets are seeded (M6), placed furniture renders as a correctly
 * sized box. It proves placement, collision and occlusion without pretending
 * to be a finished model.
 */
function buildPlaceholderItem(
  position: [number, number, number],
  yaw: number,
  size: [number, number, number],
  colorHex?: string,
): THREE.Mesh {
  const [w, h, d] = size;
  const geometry = new THREE.BoxGeometry(w, h, d);
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(colorHex ?? '#8d8578'),
    roughness: 0.8,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(position[0], h / 2, position[2]);
  // Yaw is negated here on purpose. Our 2D geometry (geometry.ts, layout.py)
  // rotates (x, z) counter-clockwise, while a three.js rotation about +Y turns
  // the opposite way in that plane. Without the negation the rendered
  // footprint is mirrored against the one the collision solver validated.
  mesh.rotation.y = (-yaw * Math.PI) / 180;
  return mesh;
}
