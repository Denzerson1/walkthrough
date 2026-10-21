/**
 * Auto exposure for a rebuilt flat, as a phone camera does it.
 *
 * One exposure for the whole flat made the hall, lit only by bounce, near
 * black once the living room looked like the owner's photos. A camera
 * meters what it is pointed at and opens up in the dark hall, though not
 * all the way (a dim room still looks dim), and stops down towards a
 * bright window. This meters a tiny render of the view every few frames
 * and eases the exposure towards it.
 */
import * as THREE from 'three';

const W = 32;
const H = 18;

/** How far the camera follows the light: 0 never, 1 to the same brightness everywhere. */
export const ADAPT = 0.6;

/** How far it opens up at most: the hall, lit only by bounce, stays a darker room. */
export const MAX_EXPOSURE = 2.2;

/**
 * The exposure for a view whose log-average linear luminance is `mean`,
 * where a view of `key` needs none. Clamped, as a camera's range is.
 */
export function targetExposure(mean: number, key: number, adapt = ADAPT): number {
  return Math.min(Math.max((key / Math.max(mean, 1e-4)) ** adapt, 0.6), MAX_EXPOSURE);
}

/** Ease `current` towards `target` with a time constant of `seconds`. */
export function approach(current: number, target: number, dt: number, seconds = 0.5): number {
  return current + (target - current) * (1 - Math.exp(-dt / seconds));
}

/** Log-average luminance of RGBA8 linear pixels, the centre weighted twice the edges. */
export function meter(pixels: Uint8Array, width: number, height: number): number {
  let sum = 0;
  let weights = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      const lum = (0.2126 * pixels[i] + 0.7152 * pixels[i + 1] + 0.0722 * pixels[i + 2]) / 255;
      const dx = (x + 0.5) / width - 0.5;
      const dy = (y + 0.5) / height - 0.5;
      const w = 1 + Math.max(0, 1 - 4 * (dx * dx + dy * dy));
      sum += w * Math.log(lum + 1e-3);
      weights += w;
    }
  }
  return Math.exp(sum / weights);
}

export class AutoExposure {
  private readonly target = new THREE.WebGLRenderTarget(W, H);
  private readonly pixels = new Uint8Array(W * H * 4);
  private frame = 0;
  private goal = 1;
  private metering = false;
  /** The latest metered log-average luminance, for calibrating `key`. */
  reading = 0;
  exposure = 1;

  constructor(private readonly key: number) {}

  /** Meter every few frames, then move the exposure a step towards the goal. */
  update(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera, dt: number) {
    if (this.frame++ % 6 === 0 && !this.metering) {
      // A render target gets linear light with no tone mapping: what the
      // sensor sees before the camera's curve.
      renderer.setRenderTarget(this.target);
      renderer.render(scene, camera);
      renderer.setRenderTarget(null);
      if (this.frame === 1) {
        // The first reading sets the exposure outright, so it is waited for.
        renderer.readRenderTargetPixels(this.target, 0, 0, W, H, this.pixels);
        this.read();
      } else {
        // The rest are not: a synchronous read stalls a phone's GPU pipeline.
        this.metering = true;
        renderer
          .readRenderTargetPixelsAsync(this.target, 0, 0, W, H, this.pixels)
          .then(() => this.read(), () => undefined)
          .finally(() => {
            this.metering = false;
          });
      }
    }
    this.exposure = this.frame === 1 ? this.goal : approach(this.exposure, this.goal, dt);
    renderer.toneMappingExposure = this.exposure;
  }

  private read() {
    this.reading = meter(this.pixels, W, H);
    this.goal = targetExposure(this.reading, this.key);
  }

  dispose() {
    this.target.dispose();
  }
}
