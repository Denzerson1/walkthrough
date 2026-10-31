/**
 * How much the view draws, stepped to what the device keeps up with.
 *
 * The frame time is averaged, as a display's refresh makes single frames
 * jump between 16.7 and 33.3 ms. An average over BUDGET_MS (under 50 fps)
 * for a second takes the pixel ratio straight to the one that should fit,
 * as the pixels drawn go with its square; at 1 splat detail goes down
 * instead. Eight seconds well inside the budget take one step back up, but
 * never to a ratio that was already too slow, so it does not see-saw.
 */

/** The average frame time aimed for, ms: 50 fps and up reads as smooth. */
export const BUDGET_MS = 20;
/** At or under this on average, there is room to spare (a 60 Hz display's 16.7). */
const SPARE_MS = 17.5;
/** Over budget this long: down; with room to spare this long: a step up, ms. */
const DOWN_AFTER_MS = 1000;
const UP_AFTER_MS = 8000;
/** How much each frame moves the average. */
const SMOOTHING = 0.1;
const RATIO_STEP = 0.25;
/** Splat detail goes down to this share, in these steps. */
const DETAIL_STEPS = [1, 0.75, 0.5];

export class Quality {
  pixelRatio: number;
  splatDetail = 1;
  /** The highest ratio still to try: lowered past each one found too slow. */
  private ceiling: number;
  private average: number | null = null;
  private overSince: number | null = null;
  private spareSince: number | null = null;

  /** `devicePixelRatio` capped at `cap` (1.5 on a phone, 2 on a desktop). */
  constructor(devicePixelRatio: number, cap: number) {
    this.ceiling = Math.max(1, Math.min(devicePixelRatio, cap));
    this.pixelRatio = this.ceiling;
  }

  /** One frame took `ms`, at time `now`; true when the quality changed. */
  frame(ms: number, now: number): boolean {
    const average = this.average === null ? ms : this.average + (ms - this.average) * SMOOTHING;
    this.average = average;
    if (average > BUDGET_MS) {
      this.spareSince = null;
      this.overSince ??= now;
      return now - this.overSince >= DOWN_AFTER_MS && this.changed(this.down(average));
    }
    this.overSince = null;
    if (average > SPARE_MS) {
      this.spareSince = null;
      return false;
    }
    this.spareSince ??= now;
    return now - this.spareSince >= UP_AFTER_MS && this.changed(this.up());
  }

  /** After a change the frames are measured afresh. */
  private changed(did: boolean): boolean {
    this.average = null;
    this.overSince = null;
    this.spareSince = null;
    return did;
  }

  private down(average: number): boolean {
    if (this.pixelRatio > 1) {
      this.ceiling = Math.min(this.ceiling, this.pixelRatio - RATIO_STEP);
      const fits = this.pixelRatio * Math.sqrt(BUDGET_MS / average);
      this.pixelRatio = Math.max(1, Math.min(this.ceiling, Math.floor(fits / RATIO_STEP) * RATIO_STEP));
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
    if (this.pixelRatio + RATIO_STEP <= this.ceiling) {
      this.pixelRatio += RATIO_STEP;
      return true;
    }
    return false;
  }
}
