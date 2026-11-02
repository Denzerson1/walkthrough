/* global process, console, window */
// Evidence for the design features, into docs/screenshots/ as JPEG (each
// under 1 MB): on an iPhone viewport and a 1440 px desktop, the title screen,
// the entry view, the menu collapsed and open, each ready style in the living
// room, a finish for the kitchen and for the wardrobe, the room labels, the
// furniture and a piece's choices, and a floor in the hall alone and then in
// the whole flat.
//
// Real Chrome with the GPU (flat-01 is too heavy for software rendering);
// a dev build of the viewer serving flat-01 (it reads window.__viewer):
//   VIEWER_URL=http://localhost:5174/walkthrough/p/flat-01 node e2e/design-shots.mjs

import { chromium, devices } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'docs', 'screenshots');
const URL = process.env.VIEWER_URL ?? 'http://localhost:5173/p/flat-01';
const STYLES = ['modern-minimal', 'scandinavian', 'mid-century', 'industrial'];

const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-gpu-blocklist'] });
for (const [label, options] of [
  ['iphone', { ...devices['iPhone 14 Pro Max'], defaultBrowserType: undefined }],
  ['desktop', { viewport: { width: 1440, height: 900 } }],
]) {
  const page = await (await browser.newContext(options)).newPage();
  const shot = (name) => page.screenshot({ path: join(OUT, `m11-${name}-${label}.jpg`), type: 'jpeg', quality: 72 });
  const pose = (x, z, yaw, pitch) => page.evaluate(([x, z, yaw, pitch]) => {
    const s = window.__viewer.state;
    s.position = [x, 1.6, z]; s.yaw = yaw; s.pitch = pitch; s.transition = null;
    window.__viewer.wake();
  }, [x, z, yaw, pitch]);
  const loaded = () => page.waitForFunction(() => [...window.__viewer.ctx.furniture.shown.values()]
    .every((s) => s.materials.length > 0), null, { timeout: 60_000 });

  await page.goto(URL);
  await page.waitForFunction(() => window.__viewer?.ctx?.model, null, { timeout: 180_000 });
  await page.waitForTimeout(800);
  await shot('title');
  await page.getByTestId('start-tour').click();
  await page.waitForTimeout(3500);
  await shot('entry-view');
  await shot('menu-collapsed');
  await page.getByTestId('design-menu-toggle').click();
  await page.waitForTimeout(500);
  await shot('menu-open');

  for (const style of STYLES) {
    await page.click(`[data-style-id="${style}"]`);
    await loaded();
    await page.getByTestId('design-menu-toggle').click();
    await pose(-0.4, -0.6, 200, -14);
    await page.waitForTimeout(1800);
    await shot(`style-${style}`);
    await page.getByTestId('design-menu-toggle').click();
  }
  await page.getByTestId('style-none').click();
  for (const [fitting, finish, at] of [
    ['kitchen', 'kitchen-sage', [1.4, 1.3, 5, -12]],
    ['wardrobe', 'wardrobe-walnut', [-1.2, 2.0, 34, -10]],
  ]) {
    await page.getByTestId(`design-tab-${fitting}`).click();
    await page.click(`[data-finish-id="${finish}"]`);
    await pose(...at);
    await page.waitForTimeout(1500);
    await shot(`finish-${fitting}`);
  }
  await page.getByTestId('design-menu-toggle').click();

  await pose(1.0, 1.8, 0, 0);
  await page.waitForTimeout(1500);
  await shot('room-labels');

  await page.getByTestId('design-menu-toggle').click();
  await page.getByTestId('design-tab-furniture').click();
  await page.waitForTimeout(1200);
  await shot('furniture');
  await page.locator('[data-slot-id="sofa-1"]').click();
  await page.waitForTimeout(1500);
  await shot('furniture-choice');
  await page.getByTestId('alternatives-back').click();

  await page.getByTestId('design-tab-floor').click();
  await page.click('[data-scope="hall"]');
  await page.click('[data-floor-id="marble-carrara"]');
  await page.waitForTimeout(4500); // the walk to the hall
  // From the living room, looking into the hall: the hall's floor alone, then all.
  await pose(0.3, 0.4, 0, -24);
  await page.waitForTimeout(1500);
  await shot('floor-hall');
  await page.click('[data-scope="all"]');
  await page.click('[data-floor-id="oak-plank-natural"]');
  await page.waitForTimeout(2500);
  await shot('floor-whole-flat');
  await page.getByTestId('menu-reset').click();
  await page.close();
}
await browser.close();
console.log(`screenshots in ${OUT}`);
