import { describe, expect, it } from 'vitest';

import { type Finish, fittingsIn, surfaceFinish } from '../src/lib/finishes';

const sage: Finish = {
  id: 'kitchen-sage',
  fitting: 'kitchen',
  name: 'Sage',
  swatches: ['#8f9d88'],
  surfaces: { 'surface-gloss': { colour: '#8f9d88' }, 'surface-stone': { colour: '#e6e2da' } },
};
const walnut: Finish = {
  id: 'wardrobe-walnut',
  fitting: 'wardrobe',
  name: 'Walnut',
  swatches: ['#643f29'],
  surfaces: { 'surface-oak': { tint: '#7a5a44' } },
};

describe('fitting finishes', () => {
  it('offers only the fittings whose materials the flat has', () => {
    expect(fittingsIn([sage, walnut], new Set(['surface-gloss', 'wall']))).toEqual(['kitchen']);
    expect(fittingsIn([sage, walnut], new Set(['surface-oak', 'surface-stone']))).toEqual(['kitchen', 'wardrobe']);
    expect(fittingsIn([sage, walnut], new Set(['wall']))).toEqual([]);
  });

  it("gives each material the chosen finish's surface, or its own", () => {
    expect(surfaceFinish([sage, walnut], 'surface-gloss')).toEqual({ colour: '#8f9d88' });
    expect(surfaceFinish([sage, walnut], 'surface-oak')).toEqual({ tint: '#7a5a44' });
    expect(surfaceFinish([sage], 'surface-oak')).toBeUndefined();
    expect(surfaceFinish([], 'surface-gloss')).toBeUndefined();
  });
});
