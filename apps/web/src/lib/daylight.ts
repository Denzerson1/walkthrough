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
  /** Multiplies the sky dome's colours. */
  skyTint?: number[] | null;
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

/** The light of a rebuilt flat at any time between its baked keys. */
export class Daylight {
  readonly keys: DaylightKey[];
  private readonly maps: THREE.Texture[];
  private readonly baked: THREE.MeshBasicMaterial[] = [];
  private readonly sites: Array<{ colour: Colours; sets: Float32Array[] }> = [];
  private sky: THREE.MeshBasicMaterial | null = null;
  private readonly uniforms = {
    lightMap2: { value: null as THREE.Texture | null },
    lightMix: { value: 0 },
    lightScale1: { value: 1 },
    lightScale2: { value: 1 },
  };

  constructor(keys: DaylightKey[], maps: THREE.Texture[], model: THREE.Object3D) {
    const order = keys.map((_, i) => i).sort((a, b) => keys[a].minutes - keys[b].minutes);
    this.keys = order.map((i) => keys[i]);
    this.maps = order.map((i) => maps[i]);
    model.traverse((node) => {
      if (!(node instanceof THREE.Mesh)) return;
      const material = node.material as THREE.MeshBasicMaterial;
      if (material.name === 'sky') this.sky = material;
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
  }

  /** Teach a baked material to mix two light maps, each at its own scale. */
  private prepare(material: THREE.MeshBasicMaterial): void {
    material.lightMapIntensity = Math.PI; // the scales are in the uniforms
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, this.uniforms);
      shader.fragmentShader = shader.fragmentShader
        .replace(
          '#include <common>',
          '#include <common>\nuniform sampler2D lightMap2;\nuniform float lightMix;\n' +
            'uniform float lightScale1;\nuniform float lightScale2;',
        )
        .replace(
          'vec4 lightMapTexel = texture2D( lightMap, vLightMapUv );',
          'vec4 lightMapTexel = mix( texture2D( lightMap, vLightMapUv ) * lightScale1, ' +
            'texture2D( lightMap2, vLightMapUv ) * lightScale2, lightMix );',
        );
    };
    material.needsUpdate = true;
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

/** Load each key's light map, as the model's own one is set up. */
export async function loadDaylight(keys: DaylightKey[], model: THREE.Object3D): Promise<Daylight> {
  const loader = new THREE.TextureLoader();
  const maps = await Promise.all(keys.map((k) => loader.loadAsync(k.lightmapUrl)));
  for (const map of maps) {
    map.flipY = false;
    map.colorSpace = THREE.SRGBColorSpace;
    map.channel = 1;
  }
  return new Daylight(keys, maps, model);
}
