/**
 * How much the view draws, stepped to what the device keeps up with.
 *
 * Frame time above 33 ms for 2 s (under 30 fps) takes a step down: first
 * the pixel ratio, a quarter at a time down to 1, then splat detail. Ten
 * seconds of frames well under that take a step back up. Stepping down
 * fast and up slowly keeps it from see-sawing.
 */

/** A frame slower than this is a slow frame, ms. */
export const SLOW_MS = 33;
/** Faster than this, it has room to spare, ms. */
const SPARE_MS = 22;
/** Slow for this long: a step down; spare for this long: a step up, ms. */
const DOWN_AFTER_MS = 2000;
const UP_AFTER_MS = 10_000;
const RATIO_STEP = 0.25;
/** Splat detail goes down to this share, in these steps. */
const DETAIL_STEPS = [1, 0.75, 0.5];

export class Quality {
  pixelRatio: number;
  splatDetail = 1;
  private readonly most: number;
  private slowSince: number | null = null;
  private spareSince: number | null = null;

  /** `devicePixelRatio` capped at `cap` (1.5 on a phone, 2 on a desktop). */
  constructor(devicePixelRatio: number, cap: number) {
    this.most = Math.max(1, Math.min(devicePixelRatio, cap));
    this.pixelRatio = this.most;
  }

  /** One frame took `ms`, at time `now`; true when the quality changed. */
  frame(ms: number, now: number): boolean {
    if (ms > SLOW_MS) {
      this.spareSince = null;
      this.slowSince ??= now;
      if (now - this.slowSince >= DOWN_AFTER_MS) {
        this.slowSince = null;
        return this.down();
      }
      return false;
    }
    this.slowSince = null;
    if (ms > SPARE_MS) {
      this.spareSince = null;
      return false;
    }
    this.spareSince ??= now;
    if (now - this.spareSince >= UP_AFTER_MS) {
      this.spareSince = null;
      return this.up();
    }
    return false;
  }

  private down(): boolean {
    if (this.pixelRatio > 1) {
      this.pixelRatio = Math.max(1, this.pixelRatio - RATIO_STEP);
      return true;
    }
    const i = DETAIL_STEPS.indexOf(this.splatDetail);
    if (i < DETAIL_STEPS.length - 1) {
      this.splatDetail = DETAIL_STEPS[i + 1];
      return true;
    }
    return false;
  }

  private up(): boolean {
    const i = DETAIL_STEPS.indexOf(this.splatDetail);
    if (i > 0) {
      this.splatDetail = DETAIL_STEPS[i - 1];
      return true;
    }
    if (this.pixelRatio < this.most) {
      this.pixelRatio = Math.min(this.most, this.pixelRatio + RATIO_STEP);
      return true;
    }
    return false;
  }
}
