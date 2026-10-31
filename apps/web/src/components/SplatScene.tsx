/**
 * The 3D scene: Spark splats plus three.js meshes for replacement floors
 * and placed furniture.
 *
 * Spark renders Gaussian splats inside a normal three.js scene, so the
 * replacement floor mesh and furniture are correctly occluded by splats
 * (brief M3) without any extra work — they share one depth buffer.
 */

import type { SplatMesh } from '@sparkjsdev/spark';
import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
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
import {
  BODY_RADIUS,
  boxInsidePolygon,
  canStand,
  moveWithCollision,
  roomAt,
} from '../lib/geometry';
import type { Manifest, Room, SunSample, Vec2 } from '../lib/manifest';
import { FLOOR_DEFAULT_COLOR, floorColor } from '../lib/floorColors';
import { apiUrl } from '../lib/api';
import {
  Daylight,
  type DaylightKey,
  decodeLightMaps,
  decodeLightSet,
  decodeVertexLight,
  loadDaylightKey,
  vertexLight,
} from '../lib/daylight';
import { AutoExposure } from '../lib/exposure';
import { fetchGlb } from '../lib/glb';
import { SunShadow, sunAt } from '../lib/sun';
import { DEFAULT_ITEM_SIZE, FurnitureLayer } from '../lib/furnitureLayer';
import type { CatalogItem } from '../lib/store';
import type { ViewerState } from '../lib/viewerState';

/** A flat rebuilt by `pipeline rebuild`: glTF plus its baked light. */
export interface BakedModel {
  url: string;
  lightmapUrl: string;
  /** The 8-bit lightmap holds light / lightmapScale (build_flat.save_lightmap). */
  lightmapScale: number;
  /** The light at other times of day, when the flat was baked at several. */
  lighting?: DaylightKey[];
  /** The sun's path, when its direct light is drawn live rather than baked (sun.ts). */
  sunPath?: SunSample[];
  /** The view out of the windows from the owner's photos, and when they were taken. */
  viewPhoto?: { url: string; minutes: number };
}

interface Props {
  manifest: Manifest;
  sceneUrl: string;
  /** When present, shown instead of the splat. */
  model?: BakedModel;
  /** Time of day for a rebuilt flat baked at several, in minutes after midnight. */
  daylight?: number;
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
  /** Fired when walking carries the camera into a different room. */
  onRoomChange?: (roomId: string) => void;
  /**
   * Live camera pose on the floor plane, for the mini plan. Throttled — this
   * crosses into React, and at 60 fps it would re-render the overlay on every
   * frame of every walk.
   */
  onPose?: (x: number, z: number, yaw: number) => void;
  /** Placed item the user has tapped, drawn with a selection ring. */
  selectedUid?: string | null;
  onSelectItem?: (uid: string | null) => void;
  /** Fired continuously while an item is dragged across the floor. */
  onMoveItem?: (uid: string, position: [number, number, number], roomId: string) => void;
}

/** Walking speed in metres per second, and how fast it reaches that speed. */
const WALK_SPEED = 1.9;
const WALK_DAMPING = 9;
/** Degrees per second for keyboard turning. */
const TURN_SPEED = 95;
/** Eye height above the floor plane. */
const EYE_HEIGHT = 1.6;

/** Key -> [strafe, forward], both in -1..1 and in camera space. */
const WALK_KEYS = new Map<string, Vec2>([
  ['w', [0, 1]],
  ['W', [0, 1]],
  ['ArrowUp', [0, 1]],
  ['s', [0, -1]],
  ['S', [0, -1]],
  ['ArrowDown', [0, -1]],
  ['a', [-1, 0]],
  ['A', [-1, 0]],
  ['ArrowLeft', [-1, 0]],
  ['d', [1, 0]],
  ['D', [1, 0]],
  ['ArrowRight', [1, 0]],
]);

/** Key -> pitch step in degrees. Negative looks down. */
const PITCH_KEYS = new Map<string, number>([
  ['r', 4],
  ['R', 4],
  ['f', -4],
  ['F', -4],
]);

/** Key -> turn direction, positive being to the left. */
const TURN_KEYS = new Map<string, number>([
  ['q', 1],
  ['Q', 1],
  ['e', -1],
  ['E', -1],
]);

interface CameraTarget {
  position: [number, number, number];
  yaw: number;
}

/** How fast a glide covers ground, and the bounds on how long one may take. */
const GLIDE_SPEED = 2.6;
const GLIDE_MIN_MS = 320;
const GLIDE_MAX_MS = 1400;

/** How long after the last change the view drops to its idle frame rate, and that rate. */
const IDLE_AFTER_MS = 1500;
const IDLE_FRAME_MS = 250;

function glideDuration(from: Vec2, to: Vec2): number {
  const metres = Math.hypot(to[0] - from[0], to[1] - from[1]);
  return Math.min(GLIDE_MAX_MS, Math.max(GLIDE_MIN_MS, (metres / GLIDE_SPEED) * 1000));
}

export function SplatScene({
  manifest,
  sceneUrl,
  model,
  daylight,
  floorUrl,
  activeRoomId,
  viewer,
  floorCatalog,
  furnitureCatalog,
  showingOriginal,
  onProgress,
  onReady,
  onError,
  onRoomChange,
  onPose,
  selectedUid = null,
  onSelectItem,
  onMoveItem,
}: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({
    yaw: 0,
    pitch: 0,
    fov: DEFAULT_FOV,
    position: [0, EYE_HEIGHT, 0] as [number, number, number],
    /** Horizontal velocity in m/s, damped towards whatever the keys ask for. */
    velocity: [0, 0] as Vec2,
    /** Unit-ish walk intent in camera space: x = strafe, y = forward. */
    intent: [0, 0] as Vec2,
    /** Turn rate in -1..1, from the keyboard. */
    turn: 0,
    keys: new Set<string>(),
    transition: null as null | {
      from: CameraTarget;
      to: CameraTarget;
      startedAt: number;
      durationMs: number;
    },
  });
  /**
   * Rooms for collision, kept in a ref because the render loop is created
   * once per scene and must not be torn down when the manifest object
   * identity changes.
   */
  const roomsRef = useRef<Room[]>([]);
  /** null until the camera has been placed, so the first room snaps. */
  const activeRoomRef = useRef<string | null>(null);
  /**
   * The render loop and the pointer handlers are set up once per scene, so
   * they reach the current callbacks through refs rather than being torn down
   * and rebuilt every time the parent re-renders.
   */
  const onRoomChangeRef = useRef(onRoomChange);
  const onSelectItemRef = useRef(onSelectItem);
  const onMoveItemRef = useRef(onMoveItem);
  const onPoseRef = useRef(onPose);
  const selectedUidRef = useRef(selectedUid);
  useEffect(() => {
    selectedUidRef.current = selectedUid;
  }, [selectedUid]);
  useEffect(() => {
    onRoomChangeRef.current = onRoomChange;
    onSelectItemRef.current = onSelectItem;
    onMoveItemRef.current = onMoveItem;
    onPoseRef.current = onPose;
  }, [onRoomChange, onSelectItem, onMoveItem, onPose]);
  const sceneRef = useRef<{
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    floorGroup: THREE.Group;
    furnitureGroup: THREE.Group;
    furniture: FurnitureLayer;
    walkCursor: THREE.Mesh;
    /** The lights placed furniture is lit by (the flat's own light is baked). */
    ambient: THREE.AmbientLight;
    key: THREE.DirectionalLight;
    splat?: SplatMesh;
    floorSplat?: SplatMesh;
    model?: THREE.Group;
    daylight?: Daylight;
    sun?: SunShadow;
    exposure?: AutoExposure;
  } | null>(null);
  // Effects that touch the rebuilt flat or the splats must run again once
  // they are in the scene.
  const [contentReady, setContentReady] = useState(false);
  // The latest slider value, for the async model load to start from.
  const daylightRef = useRef(daylight);
  useEffect(() => {
    daylightRef.current = daylight;
    if (daylight !== undefined) sceneRef.current?.daylight?.set(daylight);
  }, [daylight, contentReady]);

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
        // A rebuilt flat is hard-edged geometry: without antialiasing, window
        // sashes and linings shimmer as you move. Splats need none.
        antialias: Boolean(model),
        alpha: false,
        powerPreference: 'high-performance',
      });
    } catch {
      onError('Could not start WebGL on this device.');
      return;
    }
    // A phone's full resolution: at 2x on a 3x screen the flat looked soft
    // and its edges stepped. One that cannot keep up steps down (tick).
    let pixelRatio = Math.min(window.devicePixelRatio, 3);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    // Daylight, not the page's paper colour. Window and door openings are
    // cut out of the wall splats, so whatever is behind the scene is what you
    // see through them — and beige reads as a panel painted on the wall
    // rather than as a window.
    renderer.setClearColor(0xf3f6f8, 1);
    mount.appendChild(renderer.domElement);

    // Ambient plus a soft key so placed furniture is not flat. The real
    // environment map from the splat arrives in M6.
    const ambient = new THREE.AmbientLight(0xffffff, FURNITURE_AMBIENT);
    const key = new THREE.DirectionalLight(0xffffff, FURNITURE_KEY);
    key.position.set(2, 4, 3);
    scene.add(ambient, key);

    const floorGroup = new THREE.Group();
    const furniture = new FurnitureLayer(renderer, () => sceneRef.current?.sun?.redraw());
    const furnitureGroup = furniture.group;
    scene.add(floorGroup, furnitureGroup);

    // The ring that shows where a tap would take you. Without it, click-to-
    // walk is invisible: you have to already know it exists, and you cannot
    // tell a spot you can reach from one you cannot.
    const walkCursor = buildWalkCursor();
    walkCursor.visible = false;
    scene.add(walkCursor);

    sceneRef.current = {
      scene, camera, renderer, floorGroup, furnitureGroup, furniture, walkCursor, ambient, key,
    };
    // Development only: lets tests and debugging inspect the live scene.
    // Dev only: the scene and the walk state, so tests can stand anywhere.
    if (import.meta.env.DEV) {
      (window as unknown as { __viewer?: unknown }).__viewer = {
        get ctx() {
          return sceneRef.current;
        },
        state: stateRef.current,
      };
    }

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
    let pending = model ? 1 : floorUrl ? 2 : 1;
    // Frame times are judged from a moment after the scene is in, past the
    // first frames' uploads and shader compiles.
    let judgeFrom = Infinity;
    const settle = () => {
      if (disposed) return;
      pending -= 1;
      if (pending === 0) {
        judgeFrom = performance.now() + 2000;
        onProgress?.(1);
        onReady();
      }
    };

    if (model) {
      // Neutral tone mapping keeps colours as a photo has them, where AgX
      // greys them into the look of a render; the room's exposure is in the
      // light map's scale already.
      renderer.toneMapping = THREE.NeutralToneMapping;
      renderer.toneMappingExposure = 1;
      // Haze over the neighbourhood: distance is what makes a view read as
      // real. Nothing inside the flat is near the fog's start.
      scene.fog = new THREE.Fog(0xd6e0e8, 40, 420);
      const sunPath = model.sunPath?.length ? model.sunPath : null;
      // A rig for the time of day; also for one bake, when the sun is drawn live.
      const lighting =
        model.lighting && (model.lighting.length > 1 || sunPath) ? model.lighting : null;
      const anisotropy = renderer.capabilities.getMaxAnisotropy();
      // The time it opens at first; the other times' light (some 750 KB
      // each) after the flat is shown.
      const at = daylightRef.current ?? lighting?.[0].minutes ?? 0;
      const opening = (lighting ?? []).reduce(
        (best, key, i, keys) =>
          Math.abs(key.minutes - at) < Math.abs(keys[best].minutes - at) ? i : best,
        0,
      );
      // Every file at once rather than one after another. The model is the
      // largest of them, so how much of it has arrived is the progress shown.
      Promise.all([
        loadBakedModel(model, anisotropy, (fraction) => onProgress?.(fraction * 0.95)),
        model.viewPhoto ? loadViewPhoto(model.viewPhoto.url) : null,
        lighting ? loadDaylightKey(lighting[opening], anisotropy) : null,
      ])
        .then(([group, view, first]) => {
          if (disposed || !sceneRef.current) return;
          if (view) group.add(view);
          scene.add(group);
          sceneRef.current.model = group;
          if (lighting && first) {
            const maps = lighting.map((_, i) => (i === opening ? first.map : null));
            const skies = lighting.every((k) => k.skyUrl)
              ? lighting.map((_, i) => (i === opening ? first.sky : null))
              : null;
            if (sunPath) {
              // Framed on what was baked: the flat, not the houses round it.
              const flat = new THREE.Box3();
              group.traverse((node) => {
                if (node instanceof THREE.Mesh && node.geometry.getAttribute('uv1')) {
                  flat.expandByObject(node);
                }
              });
              sceneRef.current.sun = new SunShadow(renderer, scene, group, flat);
            }
            const { sun: shadow } = sceneRef.current;
            const sun = shadow && sunPath ? { shadow, path: sunPath } : null;
            const rig = new Daylight(lighting, maps, group, model.viewPhoto?.minutes, skies, sun);
            sceneRef.current.daylight = rig;
            rig.set(daylightRef.current ?? rig.keys[0].minutes);
            lighting.forEach((key, i) => {
              if (i === opening) return;
              loadDaylightKey(key, anisotropy)
                .then(({ map, sky }) => {
                  if (sceneRef.current?.daylight === rig) rig.provide(i, map, sky);
                })
                .catch(() => {}); // the nearest time that did arrive stands in
            });
          }
          addReflections(scene, renderer, group, manifest.rooms[0]?.waypoint.position);
          sceneRef.current.exposure = new AutoExposure(METER_KEY);
          setContentReady(true);
          settle();
        })
        .catch(() => onError('Could not load this apartment.'));
    } else {
      // Spark is most of the bundle and only splat scenes need it, so a
      // rebuilt flat on a phone neither downloads nor parses it.
      import('@sparkjsdev/spark')
        .then(({ SparkRenderer, SplatMesh }) => {
          if (disposed || !sceneRef.current) return;
          scene.add(new SparkRenderer({ renderer }));
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
          setContentReady(true);
        })
        .catch(() => onError('Could not load this apartment.'));
    }

    const onResize = () => {
      if (!mount) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    window.addEventListener('resize', onResize);

    /**
     * Integrate one frame of walking: ease the velocity towards whatever the
     * controls are asking for, move with collision, and report a room change
     * so the rest of the UI follows the person around the flat.
     */
    const stepWalk = (st: typeof stateRef.current, dt: number) => {
      const rooms = roomsRef.current;
      if (st.turn !== 0) st.yaw += st.turn * TURN_SPEED * dt;
      const [strafe, forward] = st.intent;
      const yawRad = (st.yaw * Math.PI) / 180;
      // yaw 0 looks down -Z, matching lookDirection() and the manifest.
      const fx = -Math.sin(yawRad);
      const fz = -Math.cos(yawRad);
      // Strafing right is the forward vector turned a quarter turn in XZ.
      const rx = -fz;
      const rz = fx;

      let wantX = fx * forward + rx * strafe;
      let wantZ = fz * forward + rz * strafe;
      // Diagonals must not be faster than walking straight.
      const magnitude = Math.hypot(wantX, wantZ);
      if (magnitude > 1) {
        wantX /= magnitude;
        wantZ /= magnitude;
      }

      // Exponential damping, framerate-independent: the same ease whether the
      // device is running at 30 fps or 120.
      const k = 1 - Math.exp(-WALK_DAMPING * dt);
      st.velocity[0] += (wantX * WALK_SPEED - st.velocity[0]) * k;
      st.velocity[1] += (wantZ * WALK_SPEED - st.velocity[1]) * k;

      if (Math.hypot(st.velocity[0], st.velocity[1]) < 0.003) {
        st.velocity[0] = 0;
        st.velocity[1] = 0;
        return;
      }

      const from: Vec2 = [st.position[0], st.position[2]];
      const step: Vec2 = [st.velocity[0] * dt, st.velocity[1] * dt];
      const to = rooms.length
        ? moveWithCollision(from, step, rooms, BODY_RADIUS)
        : ([from[0] + step[0], from[1] + step[1]] as Vec2);

      // Drop the component a wall refused, so walking into it does not store
      // up speed that fires the instant the user turns away.
      if (to[0] === from[0]) st.velocity[0] = 0;
      if (to[1] === from[1]) st.velocity[1] = 0;

      st.position = [to[0], EYE_HEIGHT, to[1]];

      reportRoom(to);
    };

    /** Tell the rest of the UI which room the camera is now standing in. */
    const reportRoom = (at: Vec2) => {
      const room = roomAt(at, roomsRef.current);
      if (room && room.id !== activeRoomRef.current) {
        activeRoomRef.current = room.id;
        onRoomChangeRef.current?.(room.id);
      }
    };

    // Pose reporting, throttled. The mini plan only needs to know roughly
    // where you are: pushing every frame into React would re-render the
    // overlay 60 times a second for a marker that moves a few pixels.
    let posedAt = 0;
    let posedX = Number.NaN;
    let posedZ = Number.NaN;
    let posedYaw = Number.NaN;
    const reportPose = (st: typeof stateRef.current, now: number) => {
      if (!onPoseRef.current || now - posedAt < 90) return;
      const [x, , z] = st.position;
      const moved = Math.hypot(x - posedX, z - posedZ) > 0.04;
      const turned = Math.abs(((st.yaw - posedYaw + 540) % 360) - 180) > 1.5;
      if (!moved && !turned && !Number.isNaN(posedX)) return;
      posedAt = now;
      posedX = x;
      posedZ = z;
      posedYaw = st.yaw;
      onPoseRef.current(x, z, st.yaw);
    };

    let raf = 0;
    let lastFrame = performance.now();

    // Drawing the same frame 60 times a second runs a phone hot and flat for
    // nothing. Full rate while the view moves or was touched a moment ago;
    // otherwise a few frames a second, enough for a texture that arrives
    // late or the exposure settling.
    let activeAt = lastFrame;
    let drawnAt = 0;
    const lastPose = [Number.NaN, 0, 0, 0, 0, 0];
    const wake = () => {
      activeAt = performance.now();
    };
    const wakeOn = ['pointerdown', 'pointermove', 'pointerup', 'keydown', 'wheel', 'input'] as const;
    for (const type of wakeOn) window.addEventListener(type, wake, { passive: true });
    let judged = 0;
    let slow = 0;

    const tick = () => {
      raf = requestAnimationFrame(tick);
      const st = stateRef.current;
      const now = performance.now();
      // Clamp dt so a backgrounded tab does not resume by teleporting the
      // camera the length of the flat in one step.
      const dt = Math.min(0.05, (now - lastFrame) / 1000);
      lastFrame = now;

      if (st.transition) {
        const elapsed = now - st.transition.startedAt;
        const t = easeInOutCubic(elapsed / st.transition.durationMs);
        st.position = lerpVec3(st.transition.from.position, st.transition.to.position, t);
        st.yaw = lerpAngle(st.transition.from.yaw, st.transition.to.yaw, t);
        if (elapsed >= st.transition.durationMs) {
          st.transition = null;
          // A walk can end in a different room than it started in.
          reportRoom([st.position[0], st.position[2]]);
        }
      } else {
        stepWalk(st, dt);
      }

      reportPose(st, now);

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

      const pose = [...st.position, st.yaw, st.pitch, st.fov];
      if (pose.some((v, i) => v !== lastPose[i])) {
        lastPose.splice(0, 6, ...pose);
        wake();
      }
      if (furniture.tick(now)) wake();
      if (now - activeAt > IDLE_AFTER_MS && now - drawnAt < IDLE_FRAME_MS) return;
      const sinceDrawn = Math.min(IDLE_FRAME_MS / 1000, (now - drawnAt) / 1000);
      // Below 45 fps for most of 90 frames at full rate: half a pixel ratio
      // less, until it keeps up. Never back up, which would only oscillate.
      if (now - activeAt <= IDLE_AFTER_MS && now > judgeFrom && sinceDrawn < 0.25) {
        judged += 1;
        if (sinceDrawn > 1 / 45) slow += 1;
        if (judged === 90) {
          if (slow > 45 && pixelRatio > 1) {
            pixelRatio = Math.max(1, pixelRatio - 0.5);
            renderer.setPixelRatio(pixelRatio);
          }
          judged = slow = 0;
        }
      }
      drawnAt = now;
      sceneRef.current?.exposure?.update(renderer, scene, camera, sinceDrawn);
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      for (const type of wakeOn) window.removeEventListener(type, wake);

      disposeChildren(floorGroup);
      furniture.dispose();
      walkCursor.geometry.dispose();
      (walkCursor.material as THREE.Material).dispose();
      sceneRef.current?.splat?.dispose?.();
      sceneRef.current?.floorSplat?.dispose?.();
      if (sceneRef.current?.model) disposeChildren(sceneRef.current.model);
      sceneRef.current?.exposure?.dispose();
      sceneRef.current?.sun?.dispose();
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
  }, [sceneUrl, floorUrl, model?.url]);

  // ---- pointer, wheel and keyboard interaction -------------------------
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    let dragging = false;
    let draggingUid: string | null = null;
    /** The group being dragged, moved directly so the drag stays smooth. */
    let draggingObject: THREE.Object3D | null = null;
    /** Last position inside a room, which is what gets committed. */
    let dropAt: { position: [number, number, number]; roomId: string } | null = null;
    /** What a tap would select, decided on press and applied on release. */
    let tapTarget: string | null = null;
    let grabOffset: Vec2 = [0, 0];
    let lastX = 0;
    let lastY = 0;
    /** Where and when the press started, to tell a tap from a look-drag. */
    let pressX = 0;
    let pressY = 0;
    let pressAt = 0;
    let pinchDistance = 0;
    /**
     * Fingers on the screen. A second one makes the gesture a pinch: each
     * finger's moves would otherwise turn the view by the gap between them.
     */
    const touches = new Set<number>();
    let lookPointer: number | null = null;

    /** Recompute the walk intent from whichever keys are currently held. */
    const updateIntent = () => {
      const st = stateRef.current;
      let strafe = 0;
      let forward = 0;
      let turn = 0;
      for (const key of st.keys) {
        const walk = WALK_KEYS.get(key);
        if (walk) {
          strafe += walk[0];
          forward += walk[1];
        }
        turn += TURN_KEYS.get(key) ?? 0;
      }
      st.intent = [strafe, forward];
      st.turn = Math.max(-1, Math.min(1, turn));
    };

    /**
     * Glide to the floor point under the pointer.
     *
     * The path is sampled rather than trusted: a straight line to a point in
     * the next room would otherwise pass through the wall between them. The
     * walk stops at the last sample that is still standable, so a tap across
     * a wall walks up to it instead of through it.
     */
    const walkTo = (e: PointerEvent) => {
      const st = stateRef.current;
      const target = pointerOnFloor(e);
      if (!target) return;
      const rooms = roomsRef.current;
      if (!rooms.length) return;

      const from: Vec2 = [st.position[0], st.position[2]];
      const span = Math.hypot(target[0] - from[0], target[1] - from[1]);
      if (span < 0.15) return;

      const steps = Math.max(2, Math.ceil(span / 0.1));
      let reached: Vec2 = from;
      for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        const point: Vec2 = [
          from[0] + (target[0] - from[0]) * t,
          from[1] + (target[1] - from[1]) * t,
        ];
        if (!canStand(point, rooms, BODY_RADIUS)) break;
        reached = point;
      }
      if (reached === from) return;

      st.velocity = [0, 0];
      st.transition = {
        from: { position: st.position, yaw: st.yaw },
        to: { position: [reached[0], EYE_HEIGHT, reached[1]], yaw: st.yaw },
        startedAt: performance.now(),
        durationMs: glideDuration(from, reached),
      };
    };

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const hitPoint = new THREE.Vector3();

    /** Pointer position in normalised device coordinates. */
    const setPointer = (e: PointerEvent) => {
      const rect = mount.getBoundingClientRect();
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    };

    /** The placed item under the pointer, if any. */
    const pickItem = (e: PointerEvent): THREE.Object3D | null => {
      const ctx = sceneRef.current;
      if (!ctx) return null;
      // Hidden while "hold to compare" is down. The raycaster does not test
      // visibility, so without this you could grab and drag a sofa you cannot
      // see, against the original capture.
      if (!ctx.furnitureGroup.visible) return null;
      setPointer(e);
      raycaster.setFromCamera(pointer, ctx.camera);
      const hits = raycaster.intersectObjects(ctx.furnitureGroup.children, true);
      for (const hit of hits) {
        // Walk back up to the group that carries the uid.
        let node: THREE.Object3D | null = hit.object;
        while (node && node.userData.uid === undefined) node = node.parent;
        if (!node) continue;
        // Only what is in the room you are standing in. The scene's walls are
        // splats, which the raycaster does not test against, so without this
        // the ray goes straight through them and you grab a sofa in the next
        // room — usually while trying to turn around.
        if (node.userData.roomId !== activeRoomRef.current) continue;
        return node;
      }
      return null;
    };

    /** Where the pointer ray meets the floor plane, in metres. */
    const pointerOnFloor = (e: PointerEvent): Vec2 | null => {
      const ctx = sceneRef.current;
      if (!ctx) return null;
      setPointer(e);
      raycaster.setFromCamera(pointer, ctx.camera);
      if (!raycaster.ray.intersectPlane(floorPlane, hitPoint)) return null;
      return [hitPoint.x, hitPoint.z];
    };

    /**
     * Put the ring under the pointer when it is somewhere you could stand,
     * and hide it otherwise — so a spot you cannot reach reads as unreachable
     * before you tap it rather than after.
     */
    const updateWalkCursor = (e: PointerEvent) => {
      const ctx = sceneRef.current;
      if (!ctx) return;
      const rooms = roomsRef.current;
      const floor = dragging || draggingUid ? null : pointerOnFloor(e);
      const ok = Boolean(floor && rooms.length && canStand(floor, rooms, BODY_RADIUS));
      ctx.walkCursor.visible = ok;
      if (ok && floor) ctx.walkCursor.position.set(floor[0], 0.015, floor[1]);
    };

    const onPointerLeave = () => {
      const ctx = sceneRef.current;
      if (ctx) ctx.walkCursor.visible = false;
    };

    const onPointerDown = (e: PointerEvent) => {
      if (e.pointerType === 'touch') touches.add(e.pointerId);
      if (touches.size > 1) {
        dragging = false;
        tapTarget = null;
        return;
      }
      const picked = onMoveItemRef.current ? pickItem(e) : null;

      // Dragging moves a piece ONLY when it is already the selected one.
      //
      // Otherwise a drag that happens to start over a sofa moves the sofa
      // instead of turning the view, which is most drags in a furnished room.
      // So: tap a piece to select it, then drag it. Every other drag looks
      // around, which is what a drag means the rest of the time.
      if (picked && picked.userData.uid === selectedUidRef.current) {
        const floor = pointerOnFloor(e);
        draggingUid = picked.userData.uid as string;
        draggingObject = picked;
        dropAt = null;
        // Grab offset, so the item does not jump its centre to the pointer.
        grabOffset = floor
          ? [picked.position.x - floor[0], picked.position.z - floor[1]]
          : [0, 0];
        mount.setPointerCapture(e.pointerId);
        return;
      }

      // Not a move: this is a look-drag, and on release a tap selects
      // whatever was under it (or clears the selection over empty floor).
      tapTarget = (picked?.userData.uid as string | undefined) ?? null;
      dragging = true;
      lookPointer = e.pointerId;
      lastX = e.clientX;
      lastY = e.clientY;
      pressX = e.clientX;
      pressY = e.clientY;
      pressAt = performance.now();
      mount.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      updateWalkCursor(e);
      if (draggingUid && draggingObject) {
        const floor = pointerOnFloor(e);
        if (!floor) return;
        const target: Vec2 = [floor[0] + grabOffset[0], floor[1] + grabOffset[1]];
        // A piece stays in the room it belongs to, and stays wholly inside
        // it. Testing the centre against *any* room let a sofa be dragged
        // through the wall into the next one, and let half of it hang
        // through a wall as long as its middle was still indoors.
        const room = roomsRef.current.find((r) => r.id === draggingObject?.userData.roomId);
        if (!room) return;
        const size = (draggingObject.userData.size ?? DEFAULT_ITEM_SIZE) as [
          number,
          number,
          number,
        ];
        const yawDeg = (-draggingObject.rotation.y * 180) / Math.PI;
        const plan: Vec2 = [size[0], size[2]];
        const fits = (centre: Vec2) =>
          boxInsidePolygon({ center: centre, size: plan, yaw: yawDeg }, room.floorPolygon);

        // Slide along whichever axis still fits, rather than refusing the
        // whole move. The solver places pieces flush against walls, so a
        // strict all-or-nothing test makes a sofa in its usual spot feel
        // completely stuck: every drag has a component into the wall.
        const from: Vec2 = [draggingObject.position.x, draggingObject.position.z];
        let placed: Vec2 | null = null;
        for (const candidate of [
          target,
          [target[0], from[1]] as Vec2,
          [from[0], target[1]] as Vec2,
        ]) {
          if (fits(candidate)) {
            placed = candidate;
            break;
          }
        }
        if (!placed) return;
        // Moved on the object itself rather than through the store: a store
        // write rebuilds every placed mesh, and doing that per pointer move
        // turns a drag into a slideshow once a room has a few pieces in it.
        // The store is written once, on release.
        draggingObject.position.set(placed[0], 0, placed[1]);
        dropAt = { position: [placed[0], 0, placed[1]], roomId: room.id };
        return;
      }
      if (!dragging || e.pointerId !== lookPointer) return;
      const st = stateRef.current;
      const k = dragSensitivity(st.fov);
      // A finger drags the room along with it, as every photo sphere on a
      // phone does; the mouse turns the way it moves.
      st.yaw += (e.pointerType === 'touch' ? 1 : -1) * (e.clientX - lastX) * k;
      st.pitch = clampPitch(st.pitch + (e.clientY - lastY) * k);
      lastX = e.clientX;
      lastY = e.clientY;
      // A manual look cancels an in-flight transition's rotation.
      if (st.transition) st.transition.to.yaw = st.yaw;
    };

    const onPointerUp = (e: PointerEvent) => {
      touches.delete(e.pointerId);
      // A short press that barely moved is a tap, not a look — walk there.
      // This is the only way to move on a touch device, where there is no
      // keyboard and no wheel.
      const wasTap =
        dragging &&
        !draggingUid &&
        performance.now() - pressAt < 600 &&
        // Generous, because a tap on a touchscreen always drifts a few
        // pixels and a tap that silently became a look is the single most
        // confusing thing this control can do.
        Math.hypot(e.clientX - pressX, e.clientY - pressY) < 14;
      if (wasTap) {
        // A tap on a piece selects it; a tap on open floor clears the
        // selection and walks there.
        if (tapTarget) onSelectItemRef.current?.(tapTarget);
        else {
          onSelectItemRef.current?.(null);
          walkTo(e);
        }
      }
      tapTarget = null;
      dragging = false;
      if (draggingUid && dropAt) {
        onMoveItemRef.current?.(draggingUid, dropAt.position, dropAt.roomId);
      }
      draggingUid = null;
      draggingObject = null;
      dropAt = null;
      if (mount.hasPointerCapture(e.pointerId)) mount.releasePointerCapture(e.pointerId);
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      // The wheel zooms, which is what it does everywhere else. It used to
      // walk, which read as a third way to move on top of tapping and WASD
      // and made the controls feel unpredictable.
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
    const fromUi = (e: KeyboardEvent) => {
      // Let the room strip, panels and form fields have their keys first.
      const target = e.target as HTMLElement | null;
      return Boolean(
        target &&
          target !== document.body &&
          target.closest('button, input, select, textarea, [role="tablist"], [role="dialog"]'),
      );
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (fromUi(e)) return;
      const st = stateRef.current;
      if (e.key === '+' || e.key === '=') st.fov = applyZoom(st.fov, 200);
      else if (e.key === '-') st.fov = applyZoom(st.fov, -200);
      // Pitch is a discrete nudge rather than a held rate, so that one press
      // is one repeatable amount. Looking around is normally a drag; this is
      // the keyboard-only and automated path.
      else if (PITCH_KEYS.has(e.key)) {
        st.pitch = clampPitch(st.pitch + (PITCH_KEYS.get(e.key) as number));
      } else if (WALK_KEYS.has(e.key) || TURN_KEYS.has(e.key)) {
        st.keys.add(e.key);
        // Walking overrides a glide that is still in flight.
        if (WALK_KEYS.has(e.key)) st.transition = null;
        updateIntent();
      } else return;
      e.preventDefault();
    };

    const onKeyUp = (e: KeyboardEvent) => {
      const st = stateRef.current;
      if (!st.keys.delete(e.key)) return;
      updateIntent();
    };

    // Losing focus mid-stride would otherwise leave a key held forever.
    const onBlur = () => {
      stateRef.current.keys.clear();
      updateIntent();
    };

    mount.addEventListener('pointerdown', onPointerDown);
    mount.addEventListener('pointermove', onPointerMove);
    mount.addEventListener('pointerup', onPointerUp);
    mount.addEventListener('pointercancel', onPointerUp);
    mount.addEventListener('pointerleave', onPointerLeave);
    mount.addEventListener('wheel', onWheel, { passive: false });
    mount.addEventListener('touchmove', onTouchMove, { passive: false });
    mount.addEventListener('touchend', onTouchEnd);
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);

    return () => {
      mount.removeEventListener('pointerdown', onPointerDown);
      mount.removeEventListener('pointermove', onPointerMove);
      mount.removeEventListener('pointerup', onPointerUp);
      mount.removeEventListener('pointercancel', onPointerUp);
      mount.removeEventListener('pointerleave', onPointerLeave);
      mount.removeEventListener('wheel', onWheel);
      mount.removeEventListener('touchmove', onTouchMove);
      mount.removeEventListener('touchend', onTouchEnd);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    };
  }, []);

  // ---- rooms for collision ---------------------------------------------
  useEffect(() => {
    roomsRef.current = manifest.rooms;
  }, [manifest]);

  // ---- waypoint transitions -------------------------------------------
  //
  // Only a room change that came from OUTSIDE — the room strip, a share link,
  // the assistant — glides the camera to that room's waypoint. When free
  // walking carries someone through a doorway it reports the new room itself,
  // and gliding on that would snatch the camera out of their hands the moment
  // they crossed the threshold.
  useEffect(() => {
    if (activeRoomId === activeRoomRef.current) return;
    const room = manifest.rooms.find((r) => r.id === activeRoomId);
    if (!room) return;
    const first = activeRoomRef.current === null;
    activeRoomRef.current = room.id;
    const st = stateRef.current;
    st.keys.clear();
    st.intent = [0, 0];
    st.turn = 0;
    st.velocity = [0, 0];

    // The very first placement is where the tour starts, so it snaps. Gliding
    // would mean opening the viewer at the world origin — a corner of the
    // first room, facing out of it — and sliding in from there.
    if (first) {
      st.position = [...room.waypoint.position];
      st.yaw = room.waypoint.yaw;
      st.transition = null;
      return;
    }

    st.transition = {
      from: { position: st.position, yaw: st.yaw },
      to: { position: room.waypoint.position, yaw: room.waypoint.yaw },
      startedAt: performance.now(),
      // A jump between rooms is a cut across the plan, not a walk, so it takes
      // the same beat however far it is.
      durationMs: TRANSITION_MS,
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

    // A rebuilt flat swaps the floor's texture in place, keeping its baked
    // light and shadows; drawing a separate floor over it would lose both.
    if (ctx.model) {
      // A floor mesh belongs to the room it was baked for. An open plan cut
      // into rooms afterwards (pipeline `rooms`) still has one floor, baked
      // for the scan's single room: it takes the floor chosen where you stand.
      const known = new Set(manifest.rooms.map((r) => r.id));
      const meshRooms = new Set<string>();
      ctx.model.traverse((node) => {
        if (typeof node.userData.roomId === 'string') meshRooms.add(node.userData.roomId);
      });
      for (const id of meshRooms) {
        const floorId = showingOriginal
          ? undefined
          : known.has(id)
            ? viewer.floors[id]
            : (viewer.floors[activeRoomId ?? ''] ?? Object.values(viewer.floors)[0]);
        const floor = floorId ? floorCatalog.find((f) => f.id === floorId) : undefined;
        swapModelFloor(ctx.model, id, floor);
      }
      return;
    }

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
    // showingOriginal only matters for a rebuilt flat (above).
  }, [viewer.floors, manifest, floorCatalog, showingOriginal, contentReady, activeRoomId]);

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
  //
  // The layer keeps each piece across changes: a drag moves it, a new piece
  // fades in, a removed one out, and nothing else is rebuilt.
  useEffect(() => {
    sceneRef.current?.furniture.sync(
      viewer.items,
      furnitureCatalog,
      manifest.rooms,
      selectedUid,
    );
  }, [viewer.items, furnitureCatalog, manifest.rooms, selectedUid]);

  // The flat's own sofa or bed is hidden while a style kit's piece stands in
  // its place (Slot.replaces), and back for "hold to compare".
  useEffect(() => {
    const model = sceneRef.current?.model;
    if (!model) return;
    const replaceable = new Set<string>();
    const replaced = new Set<string>();
    for (const room of manifest.rooms) {
      for (const slot of room.slots ?? []) {
        const filled = viewer.items.some((i) => i.roomId === room.id && i.slotId === slot.id);
        for (const name of slot.replaces ?? []) {
          replaceable.add(name);
          if (filled) replaced.add(name);
        }
      }
    }
    model.traverse((node) => {
      if (replaceable.has(node.name)) node.visible = showingOriginal || !replaced.has(node.name);
    });
    // A hidden sofa must not leave its shadow in the sun.
    sceneRef.current?.sun?.redraw();
  }, [viewer.items, manifest, showingOriginal, contentReady]);

  // Lamps glow from dusk: none at midday, fully from 19:30. Placed furniture
  // takes the light of the hour, as the baked flat round it does: dimmer as
  // the light maps are, its key light from where the sun is.
  useEffect(() => {
    const ctx = sceneRef.current;
    if (!ctx) return;
    const minutes = daylight ?? 12 * 60;
    ctx.furniture.setGlow(Math.min(1, Math.max(0, (minutes - 16.5 * 60) / (3 * 60))));
    const level = lightLevel(model?.lighting ?? [], minutes);
    ctx.ambient.intensity = FURNITURE_AMBIENT * level;
    ctx.key.intensity = FURNITURE_KEY * level;
    if (model?.sunPath?.length) {
      const { direction } = sunAt(model.sunPath, minutes);
      if (direction[1] > 0.02) ctx.key.position.set(...direction);
    }
  }, [daylight, contentReady, model]);

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
    // Placed furniture is a Group holding a loaded glTF scene, so this has to
    // walk the subtree rather than look only at direct children.
    child.traverse((node) => {
      if (!(node instanceof THREE.Mesh)) return;
      // Object3D.clone() SHARES geometry and materials with the original, and
      // every placement is a clone of one cached glTF scene. Disposing those
      // would blank every other copy of the same sofa, so only resources this
      // file created are freed — they carry userData.owned.
      if (node.userData.owned) node.geometry.dispose();
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      for (const material of materials) {
        if (!material.userData.owned) continue;
        // material.dispose() does NOT release its textures, so clicking
        // through the floor grid would leak three GPU textures per swap.
        const standard = material as THREE.MeshStandardMaterial;
        standard.map?.dispose();
        standard.normalMap?.dispose();
        standard.roughnessMap?.dispose();
        material.dispose();
      }
    });
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
  mesh.userData.owned = true;
  material.userData.owned = true;
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
  // The catalog stores paths the API serves ("/files/assets/..."), so they
  // need the API origin — the viewer and the API are different ports in dev.
  const resolve = (path: string) => (path.startsWith('/') ? apiUrl(path) : path);
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
    loader.load(resolve(maps.albedo), (t) => {
      if (!stillInScene()) {
        t.dispose();
        return;
      }
      t.colorSpace = THREE.SRGBColorSpace;
      material.map = configure(t);
      // Painted and stencilled floors are a neutral scan plus the catalog's
      // colour, so they keep tinting the albedo. Everywhere else the scan's
      // own colour is the material, and multiplying it by a swatch would be
      // a lie about what the floor looks like.
      if (!floor.tintAlbedo) material.color.set(0xffffff);
      material.needsUpdate = true;
    });
  }
  if (maps.normal) {
    loader.load(resolve(maps.normal), (t) => {
      if (!stillInScene()) {
        t.dispose();
        return;
      }
      material.normalMap = configure(t);
      material.needsUpdate = true;
    });
  }
  if (maps.roughness) {
    loader.load(resolve(maps.roughness), (t) => {
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
 * The metered brightness (log-average linear luminance) of a view that
 * needs no exposure change: the living room as the bake exposed it, which
 * matches the owner's photos. Darker views open up, brighter stop down.
 */
const METER_KEY = 0.33;

/** How much each surface reflects of the room around it (by material name). */
const REFLECTIVITY: Record<string, number> = {
  'surface-gloss': 0.07,
  'surface-black': 0.16,
  'surface-steel': 0.3,
  steel: 0.3,
  'surface-alu': 0.22,
  'surface-basin': 0.08,
  'surface-glass-shelf': 0.4,
  floor: 0.035,
  glass: 0.45,
};

/**
 * Reflections: the baked room seen from its middle, once, as a cube map on
 * the high-gloss fronts, the black glass, the steel and a little on the
 * oiled floor. Without them gloss reads as matte plastic, the look of a
 * render rather than a photo.
 */
function addReflections(
  scene: THREE.Scene,
  renderer: THREE.WebGLRenderer,
  model: THREE.Object3D,
  at: [number, number, number] | undefined,
): void {
  const target = new THREE.WebGLCubeRenderTarget(1024, {
    generateMipmaps: true,
    minFilter: THREE.LinearMipmapLinearFilter,
  });
  const camera = new THREE.CubeCamera(0.05, 300, target);
  camera.position.set(at?.[0] ?? 0, at?.[1] ?? 1.6, at?.[2] ?? 0);
  camera.update(renderer, scene);
  // Looked up one level down the cube's mips. Its faces are drawn without
  // antialiasing, and a roof window reflected in a front close up showed
  // their steps; a level down, each texel is four averaged.
  const envmap = THREE.ShaderChunk.envmap_fragment.replace(
    'textureCube( envMap, envMapRotation * reflectVec )',
    'textureCube( envMap, envMapRotation * reflectVec, 1.0 )',
  );
  model.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const material = node.material as THREE.MeshBasicMaterial;
    const amount = REFLECTIVITY[material.name];
    if (amount === undefined) return;
    material.envMap = target.texture;
    material.combine = THREE.MixOperation;
    material.reflectivity = amount;
    const decode = material.onBeforeCompile;
    material.onBeforeCompile = (shader, gl) => {
      decode.call(material, shader, gl);
      shader.fragmentShader = shader.fragmentShader.replace('#include <envmap_fragment>', envmap);
    };
    material.needsUpdate = true;
  });
}

/** The published flat's glTF files are meshopt-compressed (scripts/export_site.py). */
function gltfLoader(): GLTFLoader {
  return new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
}

/**
 * A rebuilt flat with its baked light.
 *
 * Every surface becomes an unlit material — its colour times the baked light
 * — so phones draw what Cycles computed, and live lights (there for placed
 * catalog furniture) cannot light the room a second time.
 */
async function loadBakedModel(
  model: BakedModel,
  anisotropy: number,
  onProgress: (fraction: number) => void,
): Promise<THREE.Group> {
  const [gltf, lightMap] = await Promise.all([
    fetchGlb(model.url, onProgress).then((buffer) =>
      gltfLoader().parseAsync(buffer, model.url.slice(0, model.url.lastIndexOf('/') + 1)),
    ),
    new THREE.TextureLoader().loadAsync(model.lightmapUrl),
  ]);
  // Same convention as the glTF's own textures, on its second UV set;
  // its encoding is undone in the shader (decodeLightMaps).
  lightMap.flipY = false;
  lightMap.colorSpace = THREE.NoColorSpace;
  lightMap.channel = 1;
  lightMap.anisotropy = anisotropy;
  const single = {
    lightMap2: { value: lightMap },
    lightMix: { value: 0 },
    lightScale1: { value: model.lightmapScale },
    lightScale2: { value: model.lightmapScale },
  };
  // The light in vertices it opens with: the neighbourhood's, and the
  // vertex-lit pieces' (glTF `_LIGHT_<time>`, which three lowercases).
  const opening = model.lighting?.find((k) => k.lightmapUrl === model.lightmapUrl)?.colours;
  gltf.scene.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const source = node.material as THREE.MeshStandardMaterial;
    // Floors and veneers seen at a glance along them stay sharp.
    if (source.map) source.map.anisotropy = anisotropy;
    const material = new THREE.MeshBasicMaterial({
      name: source.name,
      map: source.map,
      color: source.color,
      transparent: source.transparent,
      opacity: source.opacity,
    });
    // Only what was baked has the second UV set; the views outside the
    // windows are photos, shown as they are: not tone mapped either, which
    // grades a photo's blue sky to grey.
    const baked = Boolean(node.geometry.getAttribute("uv1"));
    if (!source.transparent && baked) {
      material.lightMap = lightMap;
      decodeLightMaps(material, single);
    }
    // Lit from its vertices instead: the neighbourhood (light shown as is)
    // and the vertex-lit pieces (the generated models, clothes, the pendant:
    // light in the light map's units over its scale). Shown from an RGB copy
    // of the opening time's set, which Daylight then blends between times.
    const lit = vertexLight(node);
    const attributes = node.geometry.attributes;
    const set = (opening && attributes[opening]) ||
      Object.entries(attributes).find(([name]) => name.startsWith("_light_"))?.[1];
    if (set) {
      const light = decodeLightSet(set, lit ? model.lightmapScale : 1);
      node.geometry.setAttribute("color", new THREE.BufferAttribute(light, 3));
    }
    if (lit) decodeVertexLight(material);
    if (!baked && !lit && source.name !== "glass") material.toneMapped = false;
    // A ceiling light that is on: as bright as a lit panel, whatever the exposure.
    if (source.name === "lamp") material.color.setRGB(1.6, 1.55, 1.45);
    if (source.name === "sky") material.fog = false;
    // The neighbourhood carries its sunlight and shadows in its vertices.
    material.vertexColors = Boolean(node.geometry.getAttribute("color"));
    material.userData.originalMap = source.map;
    material.userData.originalColor = source.color.clone();
    source.dispose();
    node.material = material;
  });
  return gltf.scene;
}

/**
 * The view out of the windows as the owner photographed it (view_photo.py):
 * shown as the photo is, neither lit nor tone mapped nor hazed, as the
 * camera already saw all that.
 */
async function loadViewPhoto(url: string): Promise<THREE.Object3D> {
  const gltf = await gltfLoader().loadAsync(url);
  gltf.scene.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const source = node.material as THREE.MeshBasicMaterial;
    node.material = new THREE.MeshBasicMaterial({
      name: 'view-photo',
      map: source.map,
      side: THREE.DoubleSide,
      toneMapped: false,
      fog: false,
    });
    source.dispose();
  });
  return gltf.scene;
}

/**
 * Put a catalog floor's texture on a rebuilt room's floor, or restore the
 * scanned one when `floor` is undefined. The baked light stays: only the
 * colour under it changes.
 */
function swapModelFloor(model: THREE.Group, roomId: string, floor: CatalogItem | undefined): void {
  model.traverse((node) => {
    if (!(node instanceof THREE.Mesh) || node.userData.roomId !== roomId) return;
    const material = node.material as THREE.MeshBasicMaterial;
    const swapped = material.userData.swapped as THREE.Texture | undefined;
    if (swapped) {
      swapped.dispose();
      material.userData.swapped = undefined;
    }
    material.map = material.userData.originalMap ?? null;
    material.color.copy(material.userData.originalColor);
    material.needsUpdate = true;
    // Only the latest request may land: an older download finishing late
    // would otherwise put back a floor the user has already moved on from.
    material.userData.request = floor?.id;
    const albedo = floor?.maps?.albedo;
    if (!floor || !albedo) return;
    // The floor's UVs count tiles of the rebuild's floor; rescale to this one's.
    const [baseW, baseH] = (node.userData.tileM as [number, number] | undefined) ?? [1, 1];
    const [w, h] = floor.tileSizeM ?? [baseW, baseH];
    new THREE.TextureLoader().load(apiUrl(albedo), (texture) => {
      if (material.userData.request !== floor.id || !node.parent) {
        texture.dispose();
        return;
      }
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.flipY = false;
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.repeat.set(baseW / w, baseH / h);
      texture.anisotropy = 8;
      material.map = texture;
      material.color.set(floor.tintAlbedo ? floorColor(floor) : 0xffffff);
      material.userData.swapped = texture;
      material.needsUpdate = true;
    });
  });
}

/** The ring that shows where a tap would put you. */
function buildWalkCursor(): THREE.Mesh {
  const geometry = new THREE.RingGeometry(0.17, 0.26, 40);
  geometry.rotateX(-Math.PI / 2);
  const material = new THREE.MeshBasicMaterial({
    color: 0xf7f3ec,
    transparent: true,
    opacity: 0.8,
    // Drawn over the floor rather than fighting it for the same depth.
    depthTest: false,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.renderOrder = 2;
  mesh.userData.owned = true;
  material.userData.owned = true;
  return mesh;
}

/** How brightly placed furniture is lit at midday: a fill and a key. */
const FURNITURE_AMBIENT = 1.6;
const FURNITURE_KEY = 1.1;

/**
 * The flat's light at `minutes` against midday's, from the light maps'
 * scales (each the brightest light in its map), between the keys either
 * side: 1 at noon, about 0.4 at dusk.
 */
export function lightLevel(
  keys: Array<{ minutes: number; lightmapScale: number }>,
  minutes: number,
): number {
  if (!keys.length) return 1;
  const sorted = [...keys].sort((a, b) => a.minutes - b.minutes);
  const at = (m: number) => {
    const j = sorted.findIndex((k) => k.minutes >= m);
    if (j === 0) return sorted[0].lightmapScale;
    if (j === -1) return sorted[sorted.length - 1].lightmapScale;
    const a = sorted[j - 1];
    const b = sorted[j];
    return a.lightmapScale + ((b.lightmapScale - a.lightmapScale) * (m - a.minutes)) / (b.minutes - a.minutes);
  };
  const noon = at(12 * 60);
  return noon > 0 ? at(minutes) / noon : 1;
}
