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
import type { CatalogItem } from '../lib/store';
import type { ViewerState } from '../lib/viewerState';

interface Props {
  manifest: Manifest;
  sceneUrl: string;
  activeRoomId: string | null;
  viewer: ViewerState;
  floorCatalog: CatalogItem[];
  showingOriginal: boolean;
  onProgress: (fraction: number) => void;
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
  activeRoomId,
  viewer,
  floorCatalog,
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
    const splat = new SplatMesh({
      url: sceneUrl,
      onLoad: () => {
        if (disposed) return;
        onProgress(1);
        onReady();
      },
    });
    // No orientation flip. Scenes reaching the viewer are already Y-up with the
    // floor at y=0 — the pipeline's `align` stage guarantees it and
    // docs/SPEC.md §4 states it. The 180°-about-X flip that Spark examples use
    // is for raw 3DGS exports in the Y-down convention, which never reach here.
    splat.quaternion.identity();
    scene.add(splat);
    sceneRef.current.splat = splat;

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
  }, [sceneUrl]);

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
  useEffect(() => {
    const ctx = sceneRef.current;
    if (!ctx) return;
    const group = ctx.floorGroup;
    disposeChildren(group);

    if (showingOriginal) return;

    for (const [roomId, floorId] of Object.entries(viewer.floors)) {
      const room = manifest.rooms.find((r) => r.id === roomId);
      const floor = floorCatalog.find((f) => f.id === floorId);
      if (!room || !floor || room.floorPolygon.length < 3) continue;
      group.add(buildFloorMesh(room, floor));
    }
  }, [viewer.floors, manifest, floorCatalog, showingOriginal]);

  // ---- placed furniture -------------------------------------------------
  useEffect(() => {
    const ctx = sceneRef.current;
    if (!ctx) return;
    const group = ctx.furnitureGroup;
    disposeChildren(group);
    if (showingOriginal) return;

    for (const item of viewer.items) {
      group.add(buildPlaceholderItem(item.position, item.yaw, item.colorHex));
    }
  }, [viewer.items, showingOriginal]);

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
      const material = child.material;
      if (Array.isArray(material)) material.forEach((m) => m.dispose());
      else material.dispose();
    }
  }
}

/**
 * A replacement floor is the room polygon triangulated on the y=0 plane with
 * UVs in metres, so a tile of tileSizeM repeats at its true physical size.
 */
function buildFloorMesh(room: Room, floor: CatalogItem): THREE.Mesh {
  const shape = new THREE.Shape();
  const poly = room.floorPolygon;
  shape.moveTo(poly[0][0], poly[0][1]);
  for (let i = 1; i < poly.length; i++) shape.lineTo(poly[i][0], poly[i][1]);
  shape.closePath();

  const geometry = new THREE.ShapeGeometry(shape);
  // ShapeGeometry lays the shape in XY; rotate it onto the XZ floor plane.
  geometry.rotateX(Math.PI / 2);

  const tile = floor.tileSizeM ?? [0.6, 0.6];
  const position = geometry.attributes.position;
  const uv = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i++) {
    uv[i * 2] = position.getX(i) / tile[0];
    uv[i * 2 + 1] = position.getZ(i) / tile[1];
  }
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));

  const material = new THREE.MeshStandardMaterial({
    color: fallbackColourFor(floor),
    roughness: floor.glb ? 0.7 : 0.75,
    metalness: 0.0,
    // Sit a hair above y=0 so it never z-fights with floor splats.
    polygonOffset: true,
    polygonOffsetFactor: -1,
  });

  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.y = 0.001;
  mesh.renderOrder = 1;
  loadFloorMaps(material, floor, tile);
  return mesh;
}

function loadFloorMaps(
  material: THREE.MeshStandardMaterial,
  floor: CatalogItem,
  tile: [number, number],
): void {
  const maps = floor.maps;
  if (!maps) return;
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
      t.colorSpace = THREE.SRGBColorSpace;
      material.map = configure(t);
      material.color.set(0xffffff);
      material.needsUpdate = true;
    });
  }
  if (maps.normal) {
    loader.load(maps.normal, (t) => {
      material.normalMap = configure(t);
      material.needsUpdate = true;
    });
  }
  if (maps.roughness) {
    loader.load(maps.roughness, (t) => {
      material.roughnessMap = configure(t);
      material.needsUpdate = true;
    });
  }
  void tile;
}

function fallbackColourFor(floor: CatalogItem): number {
  const byCategory: Record<string, number> = {
    wood: 0x9a7247,
    tile: 0xb9b2a6,
    stone: 0x8e8e8a,
    painted: 0xd8d3c8,
    stencilled: 0xc9c2b4,
    vintage: 0xa08a6d,
  };
  return byCategory[floor.category ?? ''] ?? 0xa89a86;
}

/**
 * Until GLB assets are seeded (M6), placed furniture renders as a correctly
 * sized box. It proves placement, collision and occlusion without pretending
 * to be a finished model.
 */
function buildPlaceholderItem(
  position: [number, number, number],
  yaw: number,
  colorHex?: string,
): THREE.Mesh {
  const geometry = new THREE.BoxGeometry(1, 0.8, 1);
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(colorHex ?? '#8d8578'),
    roughness: 0.8,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.set(position[0], 0.4, position[2]);
  mesh.rotation.y = (yaw * Math.PI) / 180;
  return mesh;
}
