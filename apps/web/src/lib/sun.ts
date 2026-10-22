/**
 * The sun of a rebuilt flat, drawn live.
 *
 * The bakes hold the sky's light and every bounce, the sun's included, but
 * not the sun's direct light (build_flat.bake): that is drawn here, from
 * where the sun stands at the time shown, with a shadow map of the flat and
 * the houses round it. So the patch it throws through a roof window walks
 * across the floor and up the wall as the time of day moves, instead of
 * fading out in one place and in at another between two bakes.
 */
import * as THREE from 'three';
import type { SunSample } from './manifest';

/** The sun at `minutes`: between the path's samples linearly, held at its ends. */
export function sunAt(
  path: SunSample[],
  minutes: number,
): { direction: [number, number, number]; light: [number, number, number] } {
  let i = 0;
  while (i < path.length - 2 && path[i + 1].minutes < minutes) i++;
  const a = path[i];
  const b = path[Math.min(i + 1, path.length - 1)];
  const span = b.minutes - a.minutes;
  const t = span > 0 ? Math.min(Math.max((minutes - a.minutes) / span, 0), 1) : 0;
  const mix = (p: number[], q: number[]) => p.map((v, k) => v + (q[k] - v) * t);
  const d = mix(a.direction, b.direction);
  const length = Math.hypot(d[0], d[1], d[2]) || 1;
  return {
    direction: [d[0] / length, d[1] / length, d[2] / length],
    light: mix(a.light, b.light) as [number, number, number],
  };
}

/** What a baked material's shader needs to add the sun (decodeLightMaps). */
export interface SunUniforms {
  sunShadow: { value: THREE.DepthTexture | null };
  /** World to the shadow map: xy its texture coordinates, z the depth there. */
  sunMatrix: { value: THREE.Matrix4 };
  /** Towards the sun. */
  sunDirection: { value: THREE.Vector3 };
  /** Its direct light on a surface facing it, in the light maps' units; zero once it has set. */
  sunLight: { value: THREE.Vector3 };
  /** The penumbra's radius, in shadow map coordinates. */
  sunSoft: { value: number };
  /** How far a surface is lifted towards the light before the shadow test, in metres. */
  sunLift: { value: number };
}

/**
 * The shader code: `sunDirect()` is the sun's direct light at this fragment.
 * Twelve taps of the shadow map, each a hardware 2x2 comparison, spread over
 * the penumbra a sun of Cycles' 1.2 degrees throws from a roof window to the
 * floor; only where the surface faces the sun.
 */
export const SUN_VERTEX = {
  declare: 'varying vec3 vSunWorld;\nvarying vec3 vSunNormal;',
  compute:
    'vSunWorld = ( modelMatrix * vec4( transformed, 1.0 ) ).xyz;\n' +
    'vSunNormal = normalize( mat3( modelMatrix ) * normal );',
};

export const SUN_FRAGMENT = `
uniform sampler2DShadow sunShadow;
uniform mat4 sunMatrix;
uniform vec3 sunDirection;
uniform vec3 sunLight;
uniform float sunSoft;
uniform float sunLift;
varying vec3 vSunWorld;
varying vec3 vSunNormal;
const vec2 SUN_TAPS[ 12 ] = vec2[](
  vec2( -0.326, -0.406 ), vec2( -0.840, -0.074 ), vec2( -0.696, 0.457 ),
  vec2( -0.203, 0.621 ), vec2( 0.962, -0.195 ), vec2( 0.473, -0.480 ),
  vec2( 0.519, 0.767 ), vec2( 0.185, -0.893 ), vec2( 0.507, 0.064 ),
  vec2( 0.896, 0.412 ), vec2( -0.322, -0.933 ), vec2( -0.792, -0.598 ) );
vec3 sunDirect() {
  vec3 n = normalize( vSunNormal );
  float facing = dot( n, sunDirection );
  if ( facing <= 0.0 || sunLight.r + sunLight.g + sunLight.b <= 0.0 ) return vec3( 0.0 );
  vec4 at = sunMatrix * vec4( vSunWorld + n * sunLift, 1.0 );
  if ( at.x < 0.0 || at.x > 1.0 || at.y < 0.0 || at.y > 1.0 ) return sunLight * facing;
  float lit = 0.0;
  for ( int i = 0; i < 12; i++ ) {
    lit += texture( sunShadow, vec3( at.xy + SUN_TAPS[ i ] * sunSoft, at.z ) );
  }
  return sunLight * facing * ( lit / 12.0 );
}
`;

/** Penumbra radius in metres: a 1.2 degree sun, two metres from window to floor. */
const PENUMBRA = 0.025;
/** How far behind the flat (towards the sun) the shadow map reaches: the houses across the street. */
const REACH = 150;
/** The layer of what casts the sun's shadow. */
const CASTS = 2;
/** Not in the sun's way: what Cycles let the light through or did not have in the bake. */
const SEE_THROUGH = new Set(['glass', 'sky', 'view-photo', 'lamp']);

/**
 * The sun's shadow map over the flat: an orthographic view along the
 * sunlight, framed on the flat (`bounds`) and reaching back far enough for
 * the neighbourhood's houses to shade it. Drawn again only when the time
 * of day changes.
 */
export class SunShadow {
  readonly uniforms: SunUniforms;
  private readonly target: THREE.WebGLRenderTarget;
  private readonly camera = new THREE.OrthographicCamera();
  private readonly depthOnly = new THREE.MeshBasicMaterial({
    colorWrite: false,
    side: THREE.DoubleSide,
  });

  constructor(
    private readonly renderer: THREE.WebGLRenderer,
    private readonly scene: THREE.Scene,
    model: THREE.Object3D,
    private readonly bounds: THREE.Box3,
    size = 2048,
  ) {
    const depth = new THREE.DepthTexture(size, size);
    depth.compareFunction = THREE.LessEqualCompare;
    depth.minFilter = depth.magFilter = THREE.LinearFilter;
    this.target = new THREE.WebGLRenderTarget(size, size, { depthTexture: depth });
    this.camera.layers.set(CASTS);
    model.traverse((node) => {
      if (!(node instanceof THREE.Mesh)) return;
      if (!SEE_THROUGH.has((node.material as THREE.Material).name)) node.layers.enable(CASTS);
    });
    this.uniforms = {
      sunShadow: { value: depth },
      sunMatrix: { value: new THREE.Matrix4() },
      sunDirection: { value: new THREE.Vector3(0, 1, 0) },
      sunLight: { value: new THREE.Vector3() },
      sunSoft: { value: 0 },
      sunLift: { value: 0.01 },
    };
  }

  /** Put the sun at `direction` (towards it) with `light`, and draw its shadow map. */
  set(direction: THREE.Vector3, light: [number, number, number]): void {
    const { uniforms, camera, bounds } = this;
    // Below the horizon it gives no direct light, and the map is not needed.
    const up = Math.min(Math.max(direction.y / 0.02, 0), 1);
    uniforms.sunLight.value.set(light[0] * up, light[1] * up, light[2] * up);
    uniforms.sunDirection.value.copy(direction);
    if (up === 0) return;

    const centre = bounds.getCenter(new THREE.Vector3());
    camera.position.copy(centre).addScaledVector(direction, REACH);
    camera.up.set(0, 1, 0);
    if (Math.abs(direction.y) > 0.99) camera.up.set(0, 0, 1);
    camera.lookAt(centre);
    camera.updateMatrixWorld();
    // Framed on the flat's corners, as the sun sees them.
    const view = camera.matrixWorldInverse;
    const box = new THREE.Box3();
    for (let k = 0; k < 8; k++) {
      const corner = new THREE.Vector3(
        k & 1 ? bounds.max.x : bounds.min.x,
        k & 2 ? bounds.max.y : bounds.min.y,
        k & 4 ? bounds.max.z : bounds.min.z,
      );
      box.expandByPoint(corner.applyMatrix4(view));
    }
    const margin = 0.2;
    camera.left = box.min.x - margin;
    camera.right = box.max.x + margin;
    camera.bottom = box.min.y - margin;
    camera.top = box.max.y + margin;
    camera.near = 0.5;
    camera.far = -box.min.z + margin;
    camera.updateProjectionMatrix();

    const toMap = new THREE.Matrix4().set(
      0.5, 0, 0, 0.5,
      0, 0.5, 0, 0.5,
      0, 0, 0.5, 0.5,
      0, 0, 0, 1,
    );
    uniforms.sunMatrix.value.multiplyMatrices(toMap, camera.projectionMatrix).multiply(view);
    uniforms.sunSoft.value = PENUMBRA / (camera.right - camera.left);

    const { renderer, scene } = this;
    const before = renderer.getRenderTarget();
    const override = scene.overrideMaterial;
    scene.overrideMaterial = this.depthOnly;
    renderer.setRenderTarget(this.target);
    renderer.clear();
    renderer.render(scene, camera);
    renderer.setRenderTarget(before);
    scene.overrideMaterial = override;
  }

  dispose(): void {
    this.target.depthTexture?.dispose();
    this.target.dispose();
    this.depthOnly.dispose();
  }
}
