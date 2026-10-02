/**
 * Camera rules for the viewer (M2).
 *
 * Two hard constraints from the brief:
 *   1. Zoom changes field of view only, clamped ~30-90°. The camera never
 *      moves off its waypoint.
 *   2. Transitions between waypoints take about 1 s and are eased.
 */

import { angleDelta } from './geometry';

export const MIN_FOV = 30;
export const MAX_FOV = 90;
export const DEFAULT_FOV = 70;
export const TRANSITION_MS = 1000;

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

/** easeInOutCubic — symmetric, no overshoot, reads as "settling". */
export function easeInOutCubic(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return c < 0.5 ? 4 * c * c * c : 1 - (-2 * c + 2) ** 3 / 2;
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
