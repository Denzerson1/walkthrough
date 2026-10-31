// The scale check for prepared furniture models: a model's box against the
// real product's size, and that size against what its category can be
// (catalog/sizes.json). Sizes are [width, height, depth] in metres, as the
// catalog's dimensionsM; a model's box is its [x, y, z], front facing +Z.

/** Largest allowed difference between a model and the real product. */
export const TOLERANCE = 0.03;

/** Uniform factors a model can be off by: units, never a stretch. */
const UNITS = { metres: 1, inches: 0.0254, centimetres: 0.01, millimetres: 0.001, feet: 0.3048 };

const near = (a, b, tolerance = TOLERANCE) => Math.abs(a - b) <= tolerance * Math.max(a, b);

/**
 * How to bring `box` (the model, [x, y, z]) to `real` ([w, h, d]): `{ ok,
 * factor, unit }` when one uniform factor (a unit conversion, else 1) puts
 * every axis within TOLERANCE, else `{ ok: false, reason }`. `flat` items
 * (rugs) are checked on their footprint only.
 */
export function fitScale(box, real, { flat = false } = {}) {
  const axes = flat ? [0, 2] : [0, 1, 2];
  for (const [unit, factor] of Object.entries(UNITS)) {
    if (axes.every((i) => near(box[i] * factor, real[i]))) return { ok: true, factor, unit };
  }
  const names = ['width', 'height', 'depth'];
  const worst = axes
    .map((i) => ({ axis: names[i], off: box[i] / real[i] - 1 }))
    .sort((a, b) => Math.abs(b.off) - Math.abs(a.off))[0];
  const swapped = near(box[0], real[2]) && near(box[2], real[0]);
  return {
    ok: false,
    reason: swapped
      ? 'width and depth are swapped: the model does not face +Z'
      : `${worst.axis} differs by ${(worst.off * 100).toFixed(1)}% and no unit explains it`,
  };
}

/** Whether `size` ([w, h, d]) is one a `category` piece can have (`ranges` from catalog/sizes.json). */
export function saneSize(size, category, ranges) {
  const range = ranges[category];
  if (!range) return { ok: false, reason: `no size range for category "${category}"` };
  const checks = [['width', size[0]], ['depth', size[2]], ...(range.flat ? [] : [['height', size[1]]])];
  for (const [name, value] of checks) {
    const [lo, hi] = range[name];
    if (value < lo || value > hi) {
      return { ok: false, reason: `${name} ${value.toFixed(2)} m is outside ${lo}–${hi} m for a ${category}` };
    }
  }
  return { ok: true };
}
