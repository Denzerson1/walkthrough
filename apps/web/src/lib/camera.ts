/**
 * Camera rules for the viewer (M2).
 *
 * Two hard constraints from the brief:
 *   1. Zoom changes field of view only, clamped ~30-90°. The camera never
 *      moves off its waypoint.
 *   2. Transitions between rooms are eased glides along the way a person
 *      would walk, through the openings between them.
 */

import { angleDelta } from './geometry';
import type { Vec2 } from './manifest';

export const MIN_FOV = 30;
export const MAX_FOV = 90;
export const DEFAULT_FOV = 70;

export function clampFov(fov: number): number {
  return Math.min(MAX_FOV, Math.max(MIN_FOV, fov));
}

/**
 * Apply a zoom gesture. `delta` is positive to zoom in.
 * Multiplicative so a pinch feels the same at every zoom level.
 */
export function applyZoom(fov: number, delta: number): number {
  return clampFov(fov * Math.exp(-delta * 0.0015));
}

/** Pitch is clamped so the user cannot roll past vertical. */
export const MIN_PITCH = -85;
export const MAX_PITCH = 85;

export function clampPitch(pitch: number): number {
  return Math.min(MAX_PITCH, Math.max(MIN_PITCH, pitch));
}

/**
 * easeInOutSine: starts and stops without a jolt, and its top speed is only
 * 1.6 times the average (a cubic's is 3), so a glide reads as a walk rather
 * than a lunge.
 */
export function easeInOut(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return (1 - Math.cos(Math.PI * c)) / 2;
}

/** 0 below `a`, 1 above `b`, smoothly in between. */
export function smoothstep(a: number, b: number, t: number): number {
  const c = Math.min(1, Math.max(0, (t - a) / (b - a)));
  return c * c * (3 - 2 * c);
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Interpolate an angle the short way round, so 350° -> 10° goes forwards. */
export function lerpAngle(a: number, b: number, t: number): number {
  return a + angleDelta(a, b) * t;
}

export function lerpVec3(
  a: [number, number, number],
  b: [number, number, number],
  t: number,
): [number, number, number] {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
}

/**
 * Drag sensitivity in degrees per pixel, scaled by the current FOV so that
 * zoomed in feels precise rather than twitchy.
 */
export function dragSensitivity(fov: number): number {
  return (fov / DEFAULT_FOV) * 0.16;
}

/** Convert yaw/pitch in degrees to a unit look direction. */
export function lookDirection(yawDeg: number, pitchDeg: number): [number, number, number] {
  const yaw = (yawDeg * Math.PI) / 180;
  const pitch = (pitchDeg * Math.PI) / 180;
  const cosPitch = Math.cos(pitch);
  // yaw 0 looks down -Z, matching the manifest convention.
  return [-Math.sin(yaw) * cosPitch, Math.sin(pitch), -Math.cos(yaw) * cosPitch];
}

/** The yaw that looks along `d` on the floor plan (x, z). */
export function headingYaw(d: Vec2): number {
  return (Math.atan2(-d[0], -d[1]) * 180) / Math.PI;
}

/** Samples per span between two of a path's points. */
const PATH_SAMPLES = 24;

/**
 * A smooth way through `points` on the floor plan: a centripetal
 * Catmull-Rom curve, which passes through every point without loops or
 * overshoot, measured so `at` moves along it at an even speed. Its heading
 * is unwrapped (it may run past 180 degrees), so it never jumps a turn.
 */
export class Path {
  readonly length: number;
  private readonly samples: Vec2[];
  private readonly distances: number[];
  /** The heading at each sample, unwrapped. */
  private readonly headings: number[];

  constructor(points: Vec2[]) {
    this.samples = [points[0]];
    for (let i = 0; i < points.length - 1; i++) {
      // The ends are carried on in a straight line.
      const p0 = points[i - 1] ?? mirror(points[i + 1], points[i]);
      const p3 = points[i + 2] ?? mirror(points[i], points[i + 1]);
      for (let k = 1; k <= PATH_SAMPLES; k++) {
        this.samples.push(catmullRom(p0, points[i], points[i + 1], p3, k / PATH_SAMPLES));
      }
    }
    this.distances = [0];
    for (let i = 1; i < this.samples.length; i++) {
      const [a, b] = [this.samples[i - 1], this.samples[i]];
      this.distances.push(this.distances[i - 1] + Math.hypot(b[0] - a[0], b[1] - a[1]));
    }
    this.length = this.distances[this.distances.length - 1];
    // Each span's heading, then at each sample the mean of the spans either side.
    const spans: number[] = [];
    for (let i = 1; i < this.samples.length; i++) {
      const [a, b] = [this.samples[i - 1], this.samples[i]];
      const yaw = headingYaw([b[0] - a[0], b[1] - a[1]]);
      spans.push(spans.length ? spans[spans.length - 1] + angleDelta(spans[spans.length - 1], yaw) : yaw);
    }
    this.headings = this.samples.map((_, i) => {
      const before = spans[i - 1] ?? spans[i] ?? 0;
      const after = spans[i] ?? before;
      return (before + after) / 2;
    });
  }

  /** The point `fraction` of the way along, and the heading there (unwrapped yaw). */
  at(fraction: number): { point: Vec2; heading: number } {
    const d = Math.min(1, Math.max(0, fraction)) * this.length;
    let i = 1;
    while (i < this.distances.length - 1 && this.distances[i] < d) i++;
    const [a, b] = [this.samples[i - 1], this.samples[i]];
    const span = this.distances[i] - this.distances[i - 1];
    const t = span > 0 ? (d - this.distances[i - 1]) / span : 0;
    return {
      point: [lerp(a[0], b[0], t), lerp(a[1], b[1], t)],
      heading: lerp(this.headings[i - 1], this.headings[i], t),
    };
  }
}

/** `p` reflected through `about`. */
function mirror(p: Vec2, about: Vec2): Vec2 {
  return [2 * about[0] - p[0], 2 * about[1] - p[1]];
}

/** Centripetal Catmull-Rom (Barry and Goldman) between p1 and p2, at t in 0..1. */
function catmullRom(p0: Vec2, p1: Vec2, p2: Vec2, p3: Vec2, t: number): Vec2 {
  const knot = (a: Vec2, b: Vec2) => Math.max(Math.hypot(b[0] - a[0], b[1] - a[1]) ** 0.5, 1e-4);
  const t1 = knot(p0, p1);
  const t2 = t1 + knot(p1, p2);
  const t3 = t2 + knot(p2, p3);
  const u = t1 + (t2 - t1) * t;
  const mix = (a: Vec2, b: Vec2, ta: number, tb: number): Vec2 => {
    const w = (u - ta) / (tb - ta);
    return [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w];
  };
  const a1 = mix(p0, p1, 0, t1);
  const a2 = mix(p1, p2, t1, t2);
  const a3 = mix(p2, p3, t2, t3);
  const b1 = mix(a1, a2, 0, t2);
  const b2 = mix(a2, a3, t1, t3);
  return mix(b1, b2, t1, t2);
}

/** Where the camera stands and looks. Pitch is left as it is when absent. */
export interface CameraPose {
  position: [number, number, number];
  yaw: number;
  pitch?: number;
}

/** A move of the camera from one pose to another over a while. */
export interface Glide {
  from: CameraPose;
  to: CameraPose;
  startedAt: number;
  durationMs: number;
  /** Followed, when present, instead of the straight line (a walk between rooms). */
  path?: Path;
  /** A tap ends it where it was going (the entry move). */
  skippable?: boolean;
  /** Set once the visitor looks round during it: from then on it only moves them. */
  lookFree?: boolean;
}

/** How far ahead along a path the gaze runs, metres. */
const LOOK_AHEAD_M = 0.6;

/**
 * The pose `t` (0..1 of its time) into a glide. On a long enough walk the
 * gaze turns from where it was to the way ahead, and from that to the
 * room's view as it arrives, as a person walking in would look; on a short
 * one, or where looking ahead would mean turning right round and back, it
 * turns straight from one view to the other.
 */
export function glidePose(g: Glide, t: number): CameraPose {
  const k = easeInOut(t);
  const y = lerp(g.from.position[1], g.to.position[1], k);
  const pitch =
    g.from.pitch !== undefined && g.to.pitch !== undefined ? lerp(g.from.pitch, g.to.pitch, k) : undefined;
  if (!g.path || g.path.length === 0) {
    return {
      position: lerpVec3(g.from.position, g.to.position, k),
      yaw: lerpAngle(g.from.yaw, g.to.yaw, k),
      pitch,
    };
  }
  const path = g.path;
  const { point } = path.at(k);
  // As plain numbers near the path's own unwrapped heading, so every blend
  // below is continuous: no short-way-round flips.
  const first = path.at(0).heading;
  const middle = path.at(0.5).heading;
  const last = path.at(1).heading;
  const from = first + angleDelta(first, g.from.yaw);
  const to = last + angleDelta(last, g.to.yaw);
  const straight = from + angleDelta(g.from.yaw, g.to.yaw);
  const away = Math.max(Math.abs(middle - from), Math.abs(to - middle));
  const lookAhead =
    Math.abs(to - straight) > 1 ? 0 : smoothstep(1.5, 3.5, path.length) * (1 - smoothstep(70, 130, away));
  const ahead = path.at(k + LOOK_AHEAD_M / path.length).heading;
  const walking = lerp(lerp(from, ahead, smoothstep(0, 0.35, t)), to, smoothstep(0.5, 1, t));
  return {
    position: [point[0], y, point[1]],
    yaw: lerp(lerp(from, straight, k), walking, lookAhead),
    pitch,
  };
}

/** How long a walk between rooms takes over `metres`: unhurried, but not a wait. */
export function walkDuration(metres: number): number {
  return Math.min(2600, Math.max(1100, 700 + 330 * metres));
}
