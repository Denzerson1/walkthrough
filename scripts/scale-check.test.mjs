import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { fitScale, saneSize } from './scale-check.mjs';

const ranges = JSON.parse(readFileSync(new URL('../catalog/sizes.json', import.meta.url)));

test('a model in metres within 3% of the product passes as it is', () => {
  assert.deepEqual(fitScale([1.682, 0.83, 0.86], [1.651, 0.813, 0.838]).factor, 1);
});

test('a model in inches is converted, uniformly', () => {
  const fit = fitScale([65, 32, 33], [1.651, 0.813, 0.838]);
  assert.equal(fit.ok, true);
  assert.equal(fit.unit, 'inches');
});

test('one axis off by more than 3% fails: nothing is stretched', () => {
  const fit = fitScale([1.65, 0.9, 0.84], [1.651, 0.813, 0.838]);
  assert.equal(fit.ok, false);
  assert.match(fit.reason, /height differs by 10\.7%/);
});

test('just outside the tolerance fails, just inside passes', () => {
  assert.equal(fitScale([1.0, 1.0, 1.0], [1.035, 1.0, 1.0]).ok, false);
  assert.equal(fitScale([1.0, 1.0, 1.0], [1.025, 1.0, 1.0]).ok, true);
});

test('a model turned a quarter is reported as not facing +Z', () => {
  const fit = fitScale([0.84, 0.81, 1.65], [1.651, 0.813, 0.838]);
  assert.equal(fit.ok, false);
  assert.match(fit.reason, /does not face \+Z/);
});

test('a rug is checked on its footprint only', () => {
  assert.equal(fitScale([2.4, 0.05, 1.53], [2.406, 0.01, 1.539], { flat: true }).ok, true);
});

test('sizes outside a category are rejected', () => {
  assert.equal(saneSize([0.3, 1.2, 0.3], 'floor_lamp', ranges).ok, false);
  assert.equal(saneSize([0.3, 1.6, 0.3], 'floor_lamp', ranges).ok, true);
  assert.equal(saneSize([1.2, 0.75, 0.9], 'dining_table', ranges).ok, true);
  assert.match(saneSize([1.2, 0.7, 0.9], 'dining_table', ranges).reason, /height 0\.70 m/);
  assert.equal(saneSize([2.2, 1.0, 2.1], 'bed', ranges).ok, false);
  assert.equal(saneSize([1.6, 0.4, 2.0], 'unknown', ranges).ok, false);
});
