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
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
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
import type { Manifest, Room, Vec2 } from '../lib/manifest';
import { FLOOR_DEFAULT_COLOR, floorColor } from '../lib/floorColors';
import { apiUrl } from '../lib/api';
import type { CatalogItem } from '../lib/store';
import type { PlacedItem, ViewerState } from '../lib/viewerState';

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

function glideDuration(from: Vec2, to: Vec2): number {
  const metres = Math.hypot(to[0] - from[0], to[1] - from[1]);
  return Math.min(GLIDE_MAX_MS, Math.max(GLIDE_MIN_MS, (metres / GLIDE_SPEED) * 1000));
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
    walkCursor: THREE.Mesh;
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
    // Daylight, not the page's paper colour. Window and door openings are
    // cut out of the wall splats, so whatever is behind the scene is what you
    // see through them — and beige reads as a panel painted on the wall
    // rather than as a window.
    renderer.setClearColor(0xf3f6f8, 1);
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

    // The ring that shows where a tap would take you. Without it, click-to-
    // walk is invisible: you have to already know it exists, and you cannot
    // tell a spot you can reach from one you cannot.
    const walkCursor = buildWalkCursor();
    walkCursor.visible = false;
    scene.add(walkCursor);

    sceneRef.current = { scene, camera, renderer, floorGroup, furnitureGroup, walkCursor };

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
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);

      disposeChildren(floorGroup);
      disposeChildren(furnitureGroup);
      walkCursor.geometry.dispose();
      (walkCursor.material as THREE.Material).dispose();
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
  //
  // Rebuilt whenever the set of items changes. A drag updates a single item's
  // position, which lands here as a new items array, so the mesh follows the
  // pointer without the drag handler reaching into the scene graph itself.
  useEffect(() => {
    const ctx = sceneRef.current;
    if (!ctx) return;
    const group = ctx.furnitureGroup;
    disposeChildren(group);

    for (const item of viewer.items) {
      const entry = furnitureCatalog.find((f) => f.id === item.itemId);
      const size = (entry?.dimensionsM ?? DEFAULT_ITEM_SIZE) as [number, number, number];
      // A placement's own recolour wins; otherwise the catalog's colour is
      // what the item is described as being made of, and the kit's meshes
      // ship in arbitrary colours that have nothing to do with it.
      const color = item.colorHex ?? entry?.colors?.[0];
      group.add(
        buildPlacedItem(item, size, entry?.glb, color, item.uid === selectedUid, entry?.shape),
      );
    }
  }, [viewer.items, furnitureCatalog, selectedUid]);

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
 * Loaded GLB scenes, keyed by URL.
 *
 * Shared across every placement and every mount: the same sofa dropped four
 * times fetches one file. Entries are clones on use, so a placement can be
 * recoloured without touching the cached original.
 */
const glbCache = new Map<string, Promise<THREE.Group>>();

function loadGlb(url: string): Promise<THREE.Group> {
  let pending = glbCache.get(url);
  if (!pending) {
    pending = new Promise<THREE.Group>((resolve, reject) => {
      new GLTFLoader().load(
        apiUrl(url),
        (gltf) => resolve(gltf.scene),
        undefined,
        (err) => reject(err),
      );
    });
    // A failed fetch must not poison the cache: the next placement should try
    // again rather than inheriting a rejected promise forever.
    pending.catch(() => glbCache.delete(url));
    glbCache.set(url, pending);
  }
  return pending;
}

/**
 * Fit `model` into a box of `size` metres, standing on the floor.
 *
 * Scaled uniformly by the tightest of the three ratios rather than stretched
 * to fill, because the catalog's dimensionsM is the box the layout solver
 * validated against — the mesh has to fit inside it, and a sofa squashed to
 * hit the number exactly looks worse than one slightly smaller than it.
 */
function fitToBox(model: THREE.Object3D, size: [number, number, number]): void {
  const bounds = new THREE.Box3().setFromObject(model);
  const extent = bounds.getSize(new THREE.Vector3());
  if (extent.x < 1e-6 || extent.y < 1e-6 || extent.z < 1e-6) return;

  const scale = Math.min(size[0] / extent.x, size[1] / extent.y, size[2] / extent.z);
  model.scale.setScalar(scale);

  // Re-measure after scaling and drop the model onto y = 0, centred in plan.
  const scaled = new THREE.Box3().setFromObject(model);
  const centre = scaled.getCenter(new THREE.Vector3());
  model.position.x -= centre.x;
  model.position.z -= centre.z;
  model.position.y -= scaled.min.y;
}

/** Tint every material in a cloned model, for a recoloured placement. */
function tintModel(model: THREE.Object3D, colorHex: string): void {
  const color = new THREE.Color(colorHex);
  model.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    child.material = materials.map((m) => {
      const clone = (m as THREE.MeshStandardMaterial).clone();
      clone.color = color;
      clone.userData.owned = true;
      return clone;
    });
    if (!Array.isArray(child.material)) return;
    if (child.material.length === 1) child.material = child.material[0];
  });
}

/**
 * One placed item: a group carrying its uid, holding the catalog mesh once it
 * arrives and a correctly sized box until then.
 *
 * The box is not a fallback nobody sees — it is what is on screen for the
 * first frames after a drop, so it has to be the right size and sit in the
 * right place.
 */
function buildPlacedItem(
  item: PlacedItem,
  size: [number, number, number],
  glb: string | null | undefined,
  colorHex: string | undefined,
  selected: boolean,
  shape?: string,
): THREE.Group {
  const group = new THREE.Group();
  group.position.set(item.position[0], 0, item.position[2]);
  // Yaw is negated here on purpose. Our 2D geometry (geometry.ts, layout.py)
  // rotates (x, z) counter-clockwise, while a three.js rotation about +Y turns
  // the opposite way in that plane. Without the negation the rendered
  // footprint is mirrored against the one the collision solver validated.
  group.rotation.y = (-item.yaw * Math.PI) / 180;
  group.userData.uid = item.uid;
  group.userData.roomId = item.roomId;
  group.userData.size = size;

  if (shape) {
    group.add(buildProceduralItem(shape, size, colorHex ?? '#8d9390'));
    if (selected) group.add(buildSelectionRing(size));
    return group;
  }

  const placeholder = buildPlaceholderItem(size, colorHex);
  group.add(placeholder);

  if (selected) group.add(buildSelectionRing(size));

  if (glb) {
    void loadGlb(glb)
      .then((scene) => {
        // The placement may have been disposed while the mesh was in flight.
        if (!group.parent) return;
        const model = scene.clone(true);
        fitToBox(model, size);
        if (colorHex) tintModel(model, colorHex);
        group.remove(placeholder);
        placeholder.geometry.dispose();
        (placeholder.material as THREE.Material).dispose();
        group.add(model);
      })
      .catch(() => {
        // Keep the box. A missing mesh is visible as a plain shape rather
        // than as nothing at all.
      });
  }

  return group;
}

/** The correctly sized box shown before a mesh arrives, or instead of one. */
function buildPlaceholderItem(
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
  mesh.position.y = h / 2;
  mesh.userData.owned = true;
  material.userData.owned = true;
  return mesh;
}

/**
 * A modern sofa and a platform bed, built here rather than downloaded.
 *
 * Poly Haven's furniture is CC0 and photoreal but almost entirely antique:
 * across its furniture library there is no contemporary sofa and no
 * contemporary bed. Those are the two pieces a flat most needs, so they are
 * drawn as the slab forms they actually are. Honest about what they are —
 * matt blocks with soft edges, not a photograph of a sofa.
 */
function roundedBox(
  w: number,
  h: number,
  d: number,
  radius: number,
  material: THREE.Material,
): THREE.Mesh {
  // Chamfer via a tight segment count on a box is not available in three, so
  // the softness comes from a slightly inset top face instead: cheap, and at
  // furniture distance it reads as an upholstered edge.
  const geometry = new THREE.BoxGeometry(w, h, d, 1, 1, 1);
  const position = geometry.attributes.position;
  const inset = Math.min(radius, Math.min(w, d) / 4);
  for (let i = 0; i < position.count; i++) {
    if (position.getY(i) > 0) {
      position.setX(i, position.getX(i) * (1 - (inset * 2) / w));
      position.setZ(i, position.getZ(i) * (1 - (inset * 2) / d));
    }
  }
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(geometry, material);
  mesh.userData.owned = true;
  return mesh;
}

function fabric(colorHex: string, roughness = 0.92): THREE.MeshStandardMaterial {
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(colorHex),
    roughness,
    metalness: 0.0,
  });
  material.userData.owned = true;
  return material;
}

function buildProceduralItem(
  shape: string,
  size: [number, number, number],
  colorHex: string,
): THREE.Group {
  const [w, h, d] = size;
  const group = new THREE.Group();
  const body = fabric(colorHex);
  const legs = fabric('#2d2f2c', 0.55);

  if (shape === 'sofa') {
    const seatH = h * 0.46;
    const legH = h * 0.14;
    const armW = w * 0.09;

    const base = roundedBox(w, seatH - legH, d, 0.04, body);
    base.position.set(0, legH + (seatH - legH) / 2, 0);
    group.add(base);

    const back = roundedBox(w, h - seatH, d * 0.26, 0.04, body);
    back.position.set(0, seatH + (h - seatH) / 2, -d / 2 + (d * 0.26) / 2);
    group.add(back);

    for (const side of [-1, 1]) {
      const arm = roundedBox(armW, h * 0.74 - legH, d, 0.035, body);
      arm.position.set(side * (w / 2 - armW / 2), legH + (h * 0.74 - legH) / 2, 0);
      group.add(arm);
    }

    // Seat cushions, slightly proud so the seam reads.
    const cushionW = (w - armW * 2) / 3;
    for (let i = 0; i < 3; i++) {
      const cushion = roundedBox(cushionW * 0.94, h * 0.12, d * 0.66, 0.03, body);
      cushion.position.set(
        -(w - armW * 2) / 2 + cushionW * (i + 0.5),
        seatH + h * 0.05,
        d * 0.08,
      );
      group.add(cushion);
    }

    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        const leg = new THREE.Mesh(new THREE.BoxGeometry(0.05, legH, 0.05), legs);
        leg.userData.owned = true;
        leg.position.set(sx * (w / 2 - 0.1), legH / 2, sz * (d / 2 - 0.1));
        group.add(leg);
      }
    }
    return group;
  }

  // Platform bed: a low base, a mattress inset on it, a headboard, two pillows.
  const baseH = h * 0.30;
  const base = roundedBox(w, baseH, d, 0.03, legs);
  base.position.set(0, baseH / 2, 0);
  group.add(base);

  const mattress = roundedBox(w * 0.94, h * 0.26, d * 0.92, 0.05, body);
  mattress.position.set(0, baseH + (h * 0.26) / 2, d * 0.02);
  group.add(mattress);

  const head = roundedBox(w, h, d * 0.07, 0.03, body);
  head.position.set(0, h / 2, -d / 2 + (d * 0.07) / 2);
  group.add(head);

  const pillowMaterial = fabric('#efeae1');
  for (const side of [-1, 1]) {
    const pillow = roundedBox(w * 0.4, h * 0.11, d * 0.17, 0.04, pillowMaterial);
    pillow.position.set(side * w * 0.23, baseH + h * 0.26 + h * 0.05, -d / 2 + d * 0.16);
    group.add(pillow);
  }
  return group;
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

/** A ring on the floor marking the item the user has tapped. */
function buildSelectionRing(size: [number, number, number]): THREE.Mesh {
  const radius = Math.max(size[0], size[2]) / 2 + 0.06;
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
  material.userData.owned = true;
  return ring;
}
