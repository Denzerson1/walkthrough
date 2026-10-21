/**
 * The time-of-day slider for a rebuilt flat.
 *
 * The pipeline bakes the flat's light at several times of day (rebuild,
 * `sun.times`). Between two of them the viewer blends: the room's light maps
 * in the shader, the neighbourhood's per-vertex sunlight on the CPU, the sky
 * dome's tint. Blending baked light is not the same as baking the time in
 * between, but at an hour or two apart the eye does not tell.
 */
import * as THREE from 'three';

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
 * each at its own scale. The light map itself stays `material.lightMap`.
 */
export function decodeLightMaps(material: THREE.MeshBasicMaterial, uniforms: LightMapUniforms) {
  material.lightMapIntensity = Math.PI; // the scales are in the uniforms
  const decode = (map: string) =>
    `pow( texture2D( ${map}, vLightMapUv ).rgb, vec3( ${LIGHT_MAP_GAMMA.toFixed(1)} ) )`;
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        '#include <common>\nuniform sampler2D lightMap2;\nuniform float lightMix;\n' +
          'uniform float lightScale1;\nuniform float lightScale2;',
      )
      .replace(
        'vec4 lightMapTexel = texture2D( lightMap, vLightMapUv );',
        `vec4 lightMapTexel = vec4( mix( ${decode('lightMap')} * lightScale1, ` +
          `${decode('lightMap2')} * lightScale2, lightMix ), 1.0 );`,
      );
  };
  material.needsUpdate = true;
}

/**
 * The sky dome, painted from two panoramas mixed: looked up by the view
 * direction, so it sits at infinity whatever the dome's size. As the
 * photos out of the windows, it is shown as is, not tone mapped.
 */
function skyMaterial(uniforms: { sky1: { value: THREE.Texture }; sky2: { value: THREE.Texture };
  skyMix: { value: number } }): THREE.ShaderMaterial {
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
      varying vec3 vWorld;
      #define PI 3.141592653589793
      void main() {
        vec3 d = normalize( vWorld - cameraPosition );
        vec2 uv = vec2( atan( d.x, -d.z ) / ( 2.0 * PI ) + 0.5,
                        0.5 + asin( clamp( d.y, -1.0, 1.0 ) ) / PI );
        gl_FragColor = vec4( mix( texture2D( sky1, uv ).rgb, texture2D( sky2, uv ).rgb, skyMix ), 1.0 );
        #include <colorspace_fragment>
      }`,
  });
}

/** The light of a rebuilt flat at any time between its baked keys. */
export class Daylight {
  readonly keys: DaylightKey[];
  private readonly maps: THREE.Texture[];
  private readonly baked: THREE.MeshBasicMaterial[] = [];
  private readonly sites: Array<{ colour: Colours; sets: Float32Array[] }> = [];
  private sky: THREE.MeshBasicMaterial | null = null;
  private readonly skies: THREE.Texture[] | null;
  private readonly skyUniforms: {
    sky1: { value: THREE.Texture };
    sky2: { value: THREE.Texture };
    skyMix: { value: number };
  } | null = null;
  private readonly photos: THREE.MeshBasicMaterial[] = [];
  /** Per key: the photo's colour, its light relative to the light it was taken in. */
  private photoTints: number[][] = [];
  private readonly uniforms: LightMapUniforms = {
    lightMap2: { value: null },
    lightMix: { value: 0 },
    lightScale1: { value: 1 },
    lightScale2: { value: 1 },
  };

  constructor(keys: DaylightKey[], maps: THREE.Texture[], model: THREE.Object3D,
    photoMinutes?: number, skies?: THREE.Texture[] | null) {
    const order = keys.map((_, i) => i).sort((a, b) => keys[a].minutes - keys[b].minutes);
    this.keys = order.map((i) => keys[i]);
    this.maps = order.map((i) => maps[i]);
    this.skies = skies ? order.map((i) => skies[i]) : null;
    if (this.skies) {
      this.skyUniforms = { sky1: { value: this.skies[0] }, sky2: { value: this.skies[0] },
        skyMix: { value: 0 } };
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

  private prepare(material: THREE.MeshBasicMaterial): void {
    decodeLightMaps(material, this.uniforms);
    this.baked.push(material);
  }

  /** Show the flat's light at `minutes` after midnight. */
  set(minutes: number): void {
    const [i, j, t] = bracket(
      this.keys.map((k) => k.minutes),
      minutes,
    );
    for (const material of this.baked) material.lightMap = this.maps[i];
    this.uniforms.lightMap2.value = this.maps[j];
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
      this.skyUniforms.sky1.value = this.skies[i];
      this.skyUniforms.sky2.value = this.skies[j];
      this.skyUniforms.skyMix.value = t;
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
 * Load each key's light map, as the model's own one is set up, and its sky.
 * Apart from the model, so both can be fetched at once.
 */
export async function loadDaylightMaps(
  keys: DaylightKey[],
): Promise<{ maps: THREE.Texture[]; skies: THREE.Texture[] | null }> {
  const loader = new THREE.TextureLoader();
  const withSkies = keys.every((k) => k.skyUrl);
  const [maps, skies] = await Promise.all([
    Promise.all(keys.map((k) => loader.loadAsync(k.lightmapUrl))),
    withSkies ? Promise.all(keys.map((k) => loader.loadAsync(k.skyUrl as string))) : null,
  ]);
  for (const map of maps) {
    map.flipY = false;
    map.colorSpace = THREE.NoColorSpace; // decoded in the shader: decodeLightMaps
    map.channel = 1;
  }
  for (const sky of skies ?? []) {
    sky.colorSpace = THREE.SRGBColorSpace;
    // No mipmaps: the lookup's seam behind the viewer would pick the smallest.
    sky.generateMipmaps = false;
    sky.minFilter = THREE.LinearFilter;
  }
  return { maps, skies };
}
