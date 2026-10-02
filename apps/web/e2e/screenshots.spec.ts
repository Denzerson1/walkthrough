/**
 * Evidence capture for docs/screenshots/, plus a real check that the splat
 * is actually on screen rather than a blank canvas.
 *
 * Saved as JPEG, not PNG: a splat render is photographic noise, which PNG
 * cannot compress, and the brief (§2.2) forbids committing binaries over
 * 1 MB. Quality 72 keeps every shot well under that.
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
  // onLoad fires when the files are parsed, but Spark still has to build and
  // sort the splat buffers for both meshes. At about one frame per second
  // under software rendering that takes a while, and a screenshot taken too
  // early catches a half-drawn scene.
  await page.waitForTimeout(14_000);
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

/** Save under docs/screenshots as a JPEG small enough to commit. */
async function capture(
  page: import('@playwright/test').Page,
  name: string,
  fullPage = false,
) {
  const file = path.join(OUT, `${name}.jpg`);
  await page.screenshot({ path: file, type: 'jpeg', quality: 72, fullPage });
  const { statSync } = await import('node:fs');
  const bytes = statSync(file).size;
  expect(bytes, `${name}.jpg is ${(bytes / 1048576).toFixed(2)} MB`).toBeLessThan(1_000_000);
}

test('capture viewer screenshots', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);
  await capture(page, `m2-viewer-${label}`);

  // Staged state with a replacement floor.
  await page.getByTestId('tool-floors').click();
  await page.locator('[data-floor-id]').first().click();
  await page.waitForTimeout(1500);
  await capture(page, `m3-floor-staged-${label}`);

  // Close the sheet so the badge and plan are unobstructed.
  await page.getByTestId('tool-floors').click();
  await page.waitForTimeout(800);
  await capture(page, `m3-staged-badge-${label}`);
});

test('capture the second room', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);
  await page.getByTestId('room-strip').getByRole('tab', { name: /Bedroom/ }).click();
  await page.waitForTimeout(2500);
  await capture(page, `m2-bedroom-${label}`);
});

test('capture the landing page', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto('/');
  await page.waitForTimeout(1200);
  await capture(page, `m10-landing-${label}`, true);
});

test('capture the furniture panel', async ({ page }, testInfo) => {
  const label = testInfo.project.name;
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);
  await page.getByTestId('tool-furniture').click();
  await page.waitForTimeout(900);
  await capture(page, `m6-furniture-${label}`);
});

/**
 * The replacement floor must be visibly different, not merely "applied".
 *
 * The earlier version of this test only checked that the Virtually staged
 * badge appeared, which it did even while the floor mesh was back-face culled
 * and invisible. This pitches the camera down so the floor actually fills the
 * view, then requires a real pixel change.
 */
test('swapping the floor visibly changes the room', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'checked once, on desktop');
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);

  const box = await page.locator('canvas').boundingBox();
  if (!box) throw new Error('no canvas');

  // Pitch down with discrete key presses rather than a drag: at roughly one
  // frame per second under software rendering, pointermove delivery is
  // unreliable and the resulting angle varies between runs.
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  for (let i = 0; i < 12; i++) await page.keyboard.press('ArrowUp');
  await page.waitForTimeout(5000);

  // Bottom centre-right: solid floor once the view is pitched down, clear of
  // the sofa on the left and the doorway at the top right.
  const floorClip = {
    x: box.x + box.width * 0.55,
    y: box.y + box.height * 0.62,
    width: box.width * 0.33,
    height: box.height * 0.22,
  };
  const before = await page.screenshot({ clip: floorClip });

  await page.getByTestId('tool-floors').click();
  await page.locator('[data-floor-id="walnut-dark-plank"]').click();
  await page.getByTestId('tool-floors').click();
  // Software rendering runs at about 1 fps here, so give it real time to
  // produce a frame with the new floor in it.
  await page.waitForTimeout(8000);

  const after = await page.screenshot({ clip: floorClip });

  const { PNG } = await import('pngjs');
  const a = PNG.sync.read(before);
  const b = PNG.sync.read(after);
  let changed = 0;
  for (let i = 0; i < a.data.length; i += 4) {
    const d =
      Math.abs(a.data[i] - b.data[i]) +
      Math.abs(a.data[i + 1] - b.data[i + 1]) +
      Math.abs(a.data[i + 2] - b.data[i + 2]);
    if (d > 24) changed++;
  }
  const fraction = changed / (a.width * a.height);
  console.log(`floor pixels changed: ${(fraction * 100).toFixed(1)}%`);
  expect(
    fraction,
    `only ${(fraction * 100).toFixed(1)}% of the floor area changed — the new floor is probably not being drawn`,
  ).toBeGreaterThan(0.4);
});
