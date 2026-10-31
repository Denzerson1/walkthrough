/**
 * Finishes for a rebuilt flat's built-in fittings (catalog/finishes): new
 * fronts and a new worktop for the kitchen, a new veneer or lacquer for the
 * wardrobe. A finish names the flat's own materials (as build_flat.py named
 * them) and what each becomes, under the same baked light.
 */

/** What one of the flat's materials becomes. */
export interface SurfaceFinish {
  /** A plain colour in place of its texture (lacquer, quartz), sRGB. */
  colour?: string;
  /** A colour its texture is multiplied by (a stain on the veneer), sRGB. */
  tint?: string;
}

export interface Finish {
  id: string;
  /** The fitting it is for. */
  fitting: Fitting;
  name: string;
  /** Its colours for the swatch, the main one first. */
  swatches: string[];
  /** The flat's material name -> what it becomes. */
  surfaces: Record<string, SurfaceFinish>;
}

export type Fitting = 'kitchen' | 'wardrobe';

export const FITTINGS: Array<{ id: Fitting; name: string }> = [
  { id: 'kitchen', name: 'Kitchen' },
  { id: 'wardrobe', name: 'Wardrobe' },
];

/** The fittings a flat has: those with a finish for a material its model has. */
export function fittingsIn(finishes: Finish[], materials: ReadonlySet<string>): Fitting[] {
  return FITTINGS.map((f) => f.id).filter((fitting) =>
    finishes.some((f) => f.fitting === fitting && Object.keys(f.surfaces).some((m) => materials.has(m))),
  );
}

/** What the material `name` shows under the finishes chosen, or undefined for its own. */
export function surfaceFinish(chosen: Finish[], name: string): SurfaceFinish | undefined {
  for (const finish of chosen) {
    const surface = finish.surfaces[name];
    if (surface) return surface;
  }
  return undefined;
}
