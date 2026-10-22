/**
 * The time-of-day slider for a rebuilt flat.
 *
 * The pipeline bakes the flat's light at several times of day (rebuild,
 * `sun.times`). Between two of them the viewer blends: the room's light maps
 * in the shader, the neighbourhood's per-vertex sunlight on the CPU, the sky
 * dome's tint. Blending baked light is not the same as baking the time in
 * between, but at an hour or two apart the eye does not tell: with the sun's
 * direct light left out of the bakes and drawn live (sun.ts), what is blended
 * is the soft light of the sky and the bounces.
 */
import * as THREE from 'three';
import type { SunSample } from './manifest';
import { SUN_FRAGMENT, SUN_VERTEX, sunAt, type SunShadow, type SunUniforms } from './sun';

export interface DaylightKey {
  /** Minutes after midnight. */
  minutes: number;
  lightmapUrl: string;
  /** The 8-bit light map holds light / lightmapScale, exposure included. */
  lightmapScale: number;
  /** Multiplies the sky dome's colours, where there is no sky panorama. */
  skyTint?: number[] | null;
  /** The sky at this time, an equirectangular panorama (pipeline sky.py). */
  skyUrl?: string | null;
  /** Which of the model's vertex colour sets holds the neighbourhood's light then. */
  colourSet: number;
}

/** "17:30" -> 1050. */
export function toMinutes(time: string): number {
  const [h, m] = time.split(':').map(Number);
  return h * 60 + (m || 0);
}

/** 1050 -> "17:30". */
export function formatMinutes(minutes: number): string {
  const m = Math.round(minutes);
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

/** The sky's colour through the day, for the track: dawn gold, noon blue, evening amber, dusk. */
const SKY: Array<[number, [number, number, number]]> = [
  [7 * 60, [243, 196, 141]],
  [10 * 60, [214, 220, 214]],
  [12 * 60, [168, 203, 233]],
  [15 * 60, [190, 211, 230]],
  [17.5 * 60, [240, 170, 102]],
  [20 * 60, [111, 106, 160]],
  [21 * 60, [72, 70, 118]],
];

/** The sky's colour at `minutes`, between the stops above. */
export function skyColour(minutes: number): string {
  const i = SKY.findIndex(([t]) => t >= minutes);
  if (i <= 0) return `rgb(${SKY[i === 0 ? 0 : SKY.length - 1][1].join(',')})`;
  const [t0, a] = SKY[i - 1];
  const [t1, b] = SKY[i];
  const f = (minutes - t0) / (t1 - t0);
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(',')})`;
}

/** What a time of day is called (at midsummer, as the flat's sun is). */
export function mood(minutes: number): string {
  if (minutes < 10 * 60) return 'Morning';
  if (minutes < 14 * 60) return 'Midday';
  if (minutes < 16.5 * 60) return 'Afternoon';
  if (minutes < 19.5 * 60) return 'Evening';
  if (minutes < 20.5 * 60) return 'Golden hour';
  return 'Sunset';
}

/**
 * The two keys (indices into `times`, sorted ascending) a time falls
 * between, and how far from the first to the second; clamped at the ends.
 */
export function bracket(times: number[], minutes: number): [number, number, number] {
  if (times.length === 1 || minutes <= times[0]) return [0, 0, 0];
  const last = times.length - 1;
  if (minutes >= times[last]) return [last, last, 0];
  let i = 0;
  while (times[i + 1] < minutes) i++;
  return [i, i + 1, (minutes - times[i]) / (times[i + 1] - times[i])];
}

type Colours = THREE.BufferAttribute | THREE.InterleavedBufferAttribute;

/**
 * The light maps hold light ** (1 / LIGHT_MAP_GAMMA), scaled: a steeper
 * curve than sRGB's, so the dim light of a hall lit only by bounce gets
 * enough of the 256 levels (build_flat.LIGHTMAP_GAMMA must match).
 */
export const LIGHT_MAP_GAMMA = 4;

export interface LightMapUniforms {
  lightMap2: { value: THREE.Texture | null };
  lightMix: { value: number };
  lightScale1: { value: number };
  lightScale2: { value: number };
}

/**
 * Teach a baked material to decode its light map and mix in a second,
 * each at its own scale, and add the sun's direct light when it is drawn
 * live (`sun`). The light map itself stays `material.lightMap`.
 */
export function decodeLightMaps(
  material: THREE.MeshBasicMaterial,
  uniforms: LightMapUniforms,
  sun?: SunUniforms,
) {
  material.lightMapIntensity = Math.PI; // the scales are in the uniforms
  const decode = (map: string) =>
    `pow( texture2D( ${map}, vLightMapUv ).rgb, vec3( ${LIGHT_MAP_GAMMA.toFixed(1)} ) )`;
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms, sun);
    if (sun) {
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', `#include <common>\n${SUN_VERTEX.declare}`)
        .replace('#include <project_vertex>', `#include <project_vertex>\n${SUN_VERTEX.compute}`);
    }
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        '#include <common>\nuniform sampler2D lightMap2;\nuniform float lightMix;\n' +
          `uniform float lightScale1;\nuniform float lightScale2;\n${sun ? SUN_FRAGMENT : ''}`,
      )
      .replace(
        'vec4 lightMapTexel = texture2D( lightMap, vLightMapUv );',
        `vec4 lightMapTexel = vec4( mix( ${decode('lightMap')} * lightScale1, ` +
          `${decode('lightMap2')} * lightScale2, lightMix )${sun ? ' + sunDirect()' : ''}, 1.0 );`,
      );
  };
  // The same source either way, so three would share one program between them.
  material.customProgramCacheKey = () => (sun ? 'baked-sun' : 'baked');
  material.needsUpdate = true;
}

// A type, not an interface: ShaderMaterial wants an index signature.
type SkyUniforms = {
  sky1: { value: THREE.Texture };
  sky2: { value: THREE.Texture };
  skyMix: { value: number };
  /** Towards the sun. */
  sunDirection: { value: THREE.Vector3 };
  /** The sun's disc, as displayed; black when it is not drawn. */
  sunDisc: { value: THREE.Color };
};

/** The sun's angular radius, radians. */
const SUN_RADIUS = (0.27 * Math.PI) / 180;

/**
 * The sky dome, painted from two panoramas mixed: looked up by the view
 * direction, so it sits at infinity whatever the dome's size. As the
 * photos out of the windows, it is shown as is, not tone mapped. The sun
 * is drawn on it where it stands (the panoramas have none, sky.py).
 */
function skyMaterial(uniforms: SkyUniforms): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    name: 'sky',
    uniforms,
    side: THREE.DoubleSide,
    // At infinity: whatever stands beyond the dome still stands in front of the sky.
    depthWrite: false,
    vertexShader: `
      varying vec3 vWorld;
      void main() {
        vec4 world = modelMatrix * vec4( position, 1.0 );
        vWorld = world.xyz;
        gl_Position = projectionMatrix * viewMatrix * world;
      }`,
    fragmentShader: `
      uniform sampler2D sky1;
      uniform sampler2D sky2;
      uniform float skyMix;
      uniform vec3 sunDirection;
      uniform vec3 sunDisc;
      varying vec3 vWorld;
      #define PI 3.141592653589793
      void main() {
        vec3 d = normalize( vWorld - cameraPosition );
        vec2 uv = vec2( atan( d.x, -d.z ) / ( 2.0 * PI ) + 0.5,
                        0.5 + asin( clamp( d.y, -1.0, 1.0 ) ) / PI );
        vec3 sky = mix( texture2D( sky1, uv ).rgb, texture2D( sky2, uv ).rgb, skyMix );
        // The disc with a soft rim, and a little glare round it. Angles by
        // 1 - cos, half the angle squared: the cosine of a quarter degree is
        // 1 to five places, where a float's precision runs out.
        float off = 1.0 - dot( d, sunDirection );
        float r = ${(SUN_RADIUS ** 2 / 2).toExponential(4)};
        float disc = 1.0 - smoothstep( r, r * 2.6, off );
        float glare = 0.25 * exp( -off * 4000.0 );
        sky = mix( sky, sunDisc, disc ) + sunDisc * glare;
        gl_FragColor = vec4( sky, 1.0 );
        #include <colorspace_fragment>
      }`,
  });
}

/**
 * The light of a rebuilt flat at any time between its baked keys. A key
 * whose light map or sky has not arrived yet (null) is stood in for by
 * the nearest one that has, until `provide` brings it.
 */
export class Daylight {
  readonly keys: DaylightKey[];
  private readonly maps: Array<THREE.Texture | null>;
  /** Where each key given to the constructor sits in `keys`, earliest first. */
  private readonly sorted: number[];
  private shown: number | null = null;
  private readonly baked: THREE.MeshBasicMaterial[] = [];
  private readonly sites: Array<{ colour: Colours; sets: Float32Array[] }> = [];
  private sky: THREE.MeshBasicMaterial | null = null;
  private readonly skies: Array<THREE.Texture | null> | null;
  private readonly skyUniforms: SkyUniforms | null = null;
  private readonly photos: THREE.MeshBasicMaterial[] = [];
  /** Per key: the photo's colour, its light relative to the light it was taken in. */
  private photoTints: number[][] = [];
  private readonly uniforms: LightMapUniforms = {
    lightMap2: { value: null },
    lightMix: { value: 0 },
    lightScale1: { value: 1 },
    lightScale2: { value: 1 },
  };

  /** The sun drawn live, when the bakes leave its direct light out (sun.ts). */
  private readonly sun: { shadow: SunShadow; path: SunSample[] } | null;

  constructor(keys: DaylightKey[], maps: Array<THREE.Texture | null>, model: THREE.Object3D,
    photoMinutes?: number, skies?: Array<THREE.Texture | null> | null,
    sun?: { shadow: SunShadow; path: SunSample[] } | null) {
    const order = keys.map((_, i) => i).sort((a, b) => keys[a].minutes - keys[b].minutes);
    this.keys = order.map((i) => keys[i]);
    this.sorted = keys.map((_, i) => order.indexOf(i));
    this.maps = order.map((i) => maps[i]);
    this.skies = skies ? order.map((i) => skies[i]) : null;
    this.sun = sun?.path.length ? sun : null;
    if (this.skies) {
      const first = this.ready(this.skies, 0);
      this.skyUniforms = { sky1: { value: first }, sky2: { value: first },
        skyMix: { value: 0 }, sunDirection: { value: new THREE.Vector3(0, 1, 0) },
        sunDisc: { value: new THREE.Color(0, 0, 0) } };
    }
    model.traverse((node) => {
      if (!(node instanceof THREE.Mesh)) return;
      const material = node.material as THREE.MeshBasicMaterial;
      if (material.name === 'sky') {
        if (this.skyUniforms) {
          material.dispose();
          node.material = skyMaterial(this.skyUniforms);
          return;
        }
        this.sky = material;
      }
      if (material.name === 'view-photo') this.photos.push(material);
      if (material.lightMap) this.prepare(material);
      const geometry = node.geometry as THREE.BufferGeometry;
      const colour = geometry.getAttribute('color');
      if (colour) {
        // glTF's COLOR_0 loads as `color`, COLOR_n as `color_n`.
        const sets = this.keys.map((key) => {
          const attr = key.colourSet === 0 ? colour : geometry.getAttribute(`color_${key.colourSet}`);
          return Float32Array.from({ length: colour.count * colour.itemSize }, (_, k) =>
            attr ? attr.getComponent(Math.floor(k / colour.itemSize), k % colour.itemSize) : 1,
          );
        });
        this.sites.push({ colour, sets });
      }
    });
    // The photo out of the window was taken in one light; at other times it
    // takes on the neighbourhood's change in light from then, as baked.
    if (this.photos.length && this.sites.length) {
      const means = this.keys.map((_, k) => {
        const sum = [0, 0, 0];
        let n = 0;
        for (const { colour, sets } of this.sites) {
          for (let v = 0; v < colour.count; v++) {
            for (let c = 0; c < 3; c++) sum[c] += sets[k][v * colour.itemSize + c];
            n++;
          }
        }
        return sum.map((s) => s / Math.max(n, 1));
      });
      const at = photoMinutes ?? this.keys[0].minutes;
      const ref = this.keys.reduce(
        (best, key, k) =>
          Math.abs(key.minutes - at) < Math.abs(this.keys[best].minutes - at) ? k : best,
        0,
      );
      this.photoTints = means.map((m) =>
        m.map((v, c) => Math.min(Math.max(v / Math.max(means[ref][c], 1e-4), 0.12), 1.25)),
      );
    }
  }

  /** The texture of key `i` (in `keys`), or of the nearest key whose has arrived. */
  private ready(list: Array<THREE.Texture | null>, i: number): THREE.Texture {
    let best = -1;
    list.forEach((texture, k) => {
      const nearer = best < 0 ||
        Math.abs(this.keys[k].minutes - this.keys[i].minutes) <
          Math.abs(this.keys[best].minutes - this.keys[i].minutes);
      if (texture && nearer) best = k;
    });
    return list[best] as THREE.Texture;
  }

  /** Key `index` (as given to the constructor) has arrived: its light map and sky. */
  provide(index: number, map: THREE.Texture, sky: THREE.Texture | null): void {
    const k = this.sorted[index];
    this.maps[k] = map;
    if (this.skies && sky) this.skies[k] = sky;
    if (this.shown !== null) this.set(this.shown);
  }

  private prepare(material: THREE.MeshBasicMaterial): void {
    decodeLightMaps(material, this.uniforms, this.sun?.shadow.uniforms);
    this.baked.push(material);
  }

  /** Show the flat's light at `minutes` after midnight. */
  set(minutes: number): void {
    const [i, j, t] = bracket(
      this.keys.map((k) => k.minutes),
      minutes,
    );
    this.shown = minutes;
    for (const material of this.baked) material.lightMap = this.ready(this.maps, i);
    this.uniforms.lightMap2.value = this.ready(this.maps, j);
    this.uniforms.lightMix.value = t;
    this.uniforms.lightScale1.value = this.keys[i].lightmapScale;
    this.uniforms.lightScale2.value = this.keys[j].lightmapScale;
    for (const { colour, sets } of this.sites) {
      const a = sets[i];
      const b = sets[j];
      for (let v = 0; v < colour.count; v++) {
        for (let c = 0; c < colour.itemSize; c++) {
          const k = v * colour.itemSize + c;
          colour.setComponent(v, c, a[k] + (b[k] - a[k]) * t);
        }
      }
      colour.needsUpdate = true;
    }
    if (this.photoTints.length) {
      const pa = this.photoTints[i];
      const pb = this.photoTints[j];
      for (const photo of this.photos) {
        photo.color.setRGB(
          pa[0] + (pb[0] - pa[0]) * t,
          pa[1] + (pb[1] - pa[1]) * t,
          pa[2] + (pb[2] - pa[2]) * t,
        );
      }
    }
    if (this.skyUniforms && this.skies) {
      this.skyUniforms.sky1.value = this.ready(this.skies, i);
      this.skyUniforms.sky2.value = this.ready(this.skies, j);
      this.skyUniforms.skyMix.value = t;
    }
    if (this.sun) {
      const { direction, light } = sunAt(this.sun.path, minutes);
      const towards = new THREE.Vector3(...direction);
      this.sun.shadow.set(towards, light);
      if (this.skyUniforms) {
        // The disc in the sunlight's colour, as bright as a display goes,
        // fading as the sun sets (SunShadow dims its light at the horizon).
        const lit = this.sun.shadow.uniforms.sunLight.value;
        const peak = Math.max(lit.x, lit.y, lit.z);
        const shown = peak > 0 ? Math.min(peak / Math.max(...light), 1) / peak : 0;
        this.skyUniforms.sunDirection.value.copy(towards);
        this.skyUniforms.sunDisc.value.setRGB(lit.x * shown, lit.y * shown, lit.z * shown);
      }
    }
    if (this.sky) {
      const ta = this.keys[i].skyTint ?? [1, 1, 1];
      const tb = this.keys[j].skyTint ?? [1, 1, 1];
      this.sky.color.setRGB(
        ta[0] + (tb[0] - ta[0]) * t,
        ta[1] + (tb[1] - ta[1]) * t,
        ta[2] + (tb[2] - ta[2]) * t,
      );
    }
  }
}

/**
 * Load a key's light map, set up as the model's own one is, and its sky.
 * Apart from the model, so both can be fetched at once.
 */
export async function loadDaylightKey(
  key: DaylightKey,
  anisotropy = 1,
): Promise<{ map: THREE.Texture; sky: THREE.Texture | null }> {
  const loader = new THREE.TextureLoader();
  const [map, sky] = await Promise.all([
    loader.loadAsync(key.lightmapUrl),
    key.skyUrl ? loader.loadAsync(key.skyUrl) : null,
  ]);
  map.flipY = false;
  map.colorSpace = THREE.NoColorSpace; // decoded in the shader: decodeLightMaps
  map.channel = 1;
  map.anisotropy = anisotropy;
  if (sky) {
    sky.colorSpace = THREE.SRGBColorSpace;
    // No mipmaps: the lookup's seam behind the viewer would pick the smallest.
    sky.generateMipmaps = false;
    sky.minFilter = THREE.LinearFilter;
  }
  return { map, sky };
}
