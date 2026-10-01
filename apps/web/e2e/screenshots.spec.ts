/**
 * Evidence capture for docs/screenshots/, plus a real check that the splat
 * is actually on screen rather than a blank canvas.
 */

import { expect, test } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PROJECT = 'demo-01';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, '../../../docs/screenshots');

/** Settle time for the splat to stream in and the first frames to render. */
async function waitForScene(page: import('@playwright/test').Page) {
  await expect(page.locator('canvas')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('floor-plan')).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(6000);
}

/**
 * Read the WebGL canvas back and count pixels that are not the clear colour.
 * A blank scene returns ~0; a rendered splat cloud returns a large fraction.
 */
/**
 * Measure how much of the 3D view is actually painted.
 *
 * Reading back the live WebGL context is unreliable (the drawing buffer is
 * not preserved after compositing), so instead screenshot a crop of the
 * canvas area that no UI overlays cover and count non-background pixels in
 * the PNG. This cannot pass on an empty scene.
 */
async function sceneCoverage(page: import('@playwright/test').Page): Promise<number> {
  const box = await page.locator('canvas').boundingBox();
  if (!box) return -1;
  // Centre crop: avoids the floor plan (top-left), fps (top-right),
  // the header, and the room strip and toolbar along the bottom.
  const buffer = await page.screenshot({
    clip: {
      x: box.x + box.width * 0.3,
      y: box.y + box.height * 0.3,
      width: box.width * 0.4,
      height: box.height * 0.35,
    },
  });
  const { PNG } = await import('pngjs');
  const png = PNG.sync.read(buffer);
  let different = 0;
  for (let i = 0; i < png.data.length; i += 4) {
    const dr = Math.abs(png.data[i] - 20);
    const dg = Math.abs(png.data[i + 1] - 23);
    const db = Math.abs(png.data[i + 2] - 28);
    if (dr + dg + db > 24) different++;
  }
  return different / (png.width * png.height);
}

test('the splat scene actually renders pixels', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'checked once, on desktop');
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);

  const coverage = await sceneCoverage(page);
  console.log(`scene coverage: ${(coverage * 100).toFixed(1)}%`);
  // The centre of the view looks straight into the room, so a working scene
  // paints nearly all of it. Under 30% means an empty canvas.
  expect(
    coverage,
    `only ${(coverage * 100).toFixed(1)}% of the centre crop is painted`,
  ).toBeGreaterThan(0.3);
});

test('capture viewer screenshots', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);
  await page.screenshot({ path: path.join(OUT, `m2-viewer-${label}.png`) });

  // Staged state with a replacement floor.
  await page.getByTestId('tool-floors').click();
  await page.locator('[data-floor-id]').first().click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, `m3-floor-staged-${label}.png`) });

  // Close the sheet so the badge and plan are unobstructed.
  await page.getByTestId('tool-floors').click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, `m3-staged-badge-${label}.png`) });
});

test('capture the second room', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);
  await page.getByTestId('room-strip').getByRole('tab', { name: /Bedroom/ }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT, `m2-bedroom-${label}.png`) });
});

test('capture the landing page', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto('/');
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(OUT, `m10-landing-${label}.png`), fullPage: true });
});

test('capture the furniture panel', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);
  await page.getByTestId('tool-furniture').click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: path.join(OUT, `m6-furniture-${label}.png`) });
});
