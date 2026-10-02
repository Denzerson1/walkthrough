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

/**
 * These are slow by nature and the default 180 s no longer fits.
 *
 * The demo scene went from 350 k splats to 1.4 M when the flat grew to seven
 * rooms and the surfaces moved to a jittered grid. Under SwiftShader that is
 * roughly one frame per second, and every `capture()` forces a render. The
 * budget is about the renderer, not the app: a GPU runner does not need it.
 */
test.describe.configure({ timeout: 360_000 });
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, '../../../docs/screenshots');

/** Settle time for the splat to stream in and the first frames to render. */
async function waitForScene(page: import('@playwright/test').Page) {
  await expect(page.locator('canvas')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('floor-plan')).toBeVisible({ timeout: 60_000 });
  // onLoad fires when the files are parsed, but Spark still has to build and
  // sort the splat buffers for both meshes. At about one frame per second
  // under software rendering that takes a while, and a screenshot taken too
  // early catches a half-drawn scene. Scaled with the scene: 14 s was set
  // when it was a quarter the size.
  await page.waitForTimeout(24_000);
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
  // Crop the lower middle: high enough to clear the toolbar, low enough to
  // contain floor and furniture in every room. The old crop sat at eye level,
  // where a correctly rendered room is mostly blank wall.
  const buffer = await page.screenshot({
    clip: {
      x: box.x + box.width * 0.3,
      y: box.y + box.height * 0.45,
      width: box.width * 0.4,
      height: box.height * 0.33,
    },
  });
  const { PNG } = await import('pngjs');
  const png = PNG.sync.read(buffer);

  let different = 0;
  let sum = 0;
  let sumSq = 0;
  const pixels = png.width * png.height;
  for (let i = 0; i < png.data.length; i += 4) {
    const r = png.data[i];
    const g = png.data[i + 1];
    const b = png.data[i + 2];
    // Distance from the renderer's clear colour — what an empty canvas is.
    // Keep in step with setClearColor() in SplatScene.tsx.
    if (Math.abs(r - 0xf3) + Math.abs(g - 0xf6) + Math.abs(b - 0xf8) > 24) different++;
    const luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    sum += luma;
    sumSq += luma * luma;
  }

  // Second, colour-independent signal: an empty canvas is perfectly uniform.
  // On its own the clear-colour test is fragile — when the background changed
  // from near-black to near-white and this constant did not, every pixel
  // counted as "different" and the check could no longer fail at all.
  const variance = sumSq / pixels - (sum / pixels) ** 2;
  if (Math.sqrt(variance) < 4) return 0;

  return different / pixels;
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
  await page.getByTestId('room-strip').getByRole('tab', { name: /Main bedroom/ }).click();
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
  // Polls for up to 110 s on top of the scene load, and every screenshot in
  // that loop costs about a frame at roughly 1 fps under SwiftShader.
  test.setTimeout(420_000);
  await page.goto(`/p/${PROJECT}`);
  await waitForScene(page);

  const box = await page.locator('canvas').boundingBox();
  if (!box) throw new Error('no canvas');

  // Pitch down with discrete key presses rather than a drag: at roughly one
  // frame per second under software rendering, pointermove delivery is
  // unreliable and the resulting angle varies between runs. 'f' looks down —
  // the arrow keys walk now that the viewer is free-roam.
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  for (let i = 0; i < 12; i++) await page.keyboard.press('f');
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

  const { PNG } = await import('pngjs');
  const a = PNG.sync.read(before);
  const changedFraction = (shot: Buffer) => {
    const b = PNG.sync.read(shot);
    let changed = 0;
    for (let i = 0; i < a.data.length; i += 4) {
      const d =
        Math.abs(a.data[i] - b.data[i]) +
        Math.abs(a.data[i + 1] - b.data[i + 1]) +
        Math.abs(a.data[i + 2] - b.data[i + 2]);
      if (d > 24) changed++;
    }
    return changed / (a.width * a.height);
  };

  await page.getByTestId('tool-floors').click();
  await page.locator('[data-floor-id="walnut-dark-plank"]').click();
  await page.getByTestId('tool-floors').click();

  // Poll rather than wait a fixed time. The replacement floor's colour,
  // normal and roughness maps are real downloaded textures (~2.4 MB a set)
  // and software rendering here is about one frame per second, so "long
  // enough" is not a constant: a fixed 8 s wait passed on a warm HTTP cache
  // and failed on a cold one.
  //
  // Polling the pixels, rather than waiting on the three map responses,
  // because a cached texture may produce no network response at all — waiting
  // for one hung until the test's own timeout.
  let fraction = 0;
  const deadline = Date.now() + 110_000;
  while (Date.now() < deadline) {
    fraction = changedFraction(await page.screenshot({ clip: floorClip }));
    if (fraction > 0.4) break;
    await page.waitForTimeout(1500);
  }

  console.log(`floor pixels changed: ${(fraction * 100).toFixed(1)}%`);
  expect(
    fraction,
    `only ${(fraction * 100).toFixed(1)}% of the floor area changed — the new floor is probably not being drawn`,
  ).toBeGreaterThan(0.4);
});
