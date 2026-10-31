// Evidence for the design features, into docs/screenshots/ as JPEG (each
// under 1 MB): on an iPhone viewport and a 1440 px desktop, the title screen,
// the entry view, the menu collapsed and open, the "This room" tab, each
// ready style in the living room, and the room labels.
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
  await page.getByTestId('design-tab-room').click();
  await page.waitForTimeout(600);
  await shot('this-room');
  await page.getByTestId('design-menu-toggle').click();

  await pose(1.0, 1.8, 0, 0);
  await page.waitForTimeout(1500);
  await shot('room-labels');
  await page.close();
}
await browser.close();
console.log(`screenshots in ${OUT}`);
