/**
 * Representative colour for a floor before its texture maps exist.
 *
 * One table, used by both the picker swatch and the rendered floor mesh.
 * They were written separately — as CSS gradients and as hex ints — and a
 * new category would have made the swatch disagree with the floor.
 */

import type { CatalogItem } from './store';

export const FLOOR_CATEGORY_COLOR: Record<string, string> = {
  wood: '#9a7247',
  tile: '#b9b2a6',
  stone: '#8e8e8a',
  painted: '#d8d3c8',
  stencilled: '#c9c2b4',
  vintage: '#a08a6d',
};

export const FLOOR_DEFAULT_COLOR = '#a89a86';

/** The catalog entry's own colour wins; the category is only a fallback. */
export function floorColor(floor: CatalogItem): string {
  return (
    floor.baseColor ?? FLOOR_CATEGORY_COLOR[floor.category ?? ''] ?? FLOOR_DEFAULT_COLOR
  );
}
