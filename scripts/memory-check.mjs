// The memory and idle check, in Chromium with a phone's settings forced
// (iPhone viewport, touch, so the phone models load) and the CPU slowed 4x:
//
//   1. 20 style switches in the living room, through the design menu; the
//      GPU's geometries and textures (renderer.info.memory) when the last
//      kit is shown must be within 10% of when that kit was first shown,
//      and Reset must bring them back within 10% of the flat before any kit.
//   2. Once the downloads in flight have landed, nothing is drawn after 2 s
//      without input (renderer.info.render.frame stays put).
//
// Needs a dev build of the viewer (it reads window.__viewer):
//   VIEWER_URL=http://localhost:5174/walkthrough/p/flat-01 node scripts/memory-check.mjs

import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { chromium, devices } = createRequire(join(ROOT, 'apps', 'web', 'package.json'))('@playwright/test');
const URL = process.env.VIEWER_URL ?? 'http://localhost:5173/p/flat-01';
const KITS = ['modern-minimal', 'scandinavian', 'mid-century', 'industrial'];
const SWITCHES = 20;

const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-gpu-blocklist'] });
const context = await browser.newContext({ ...devices['iPhone 14 Pro Max'], defaultBrowserType: undefined });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));

const memory = () => page.evaluate(() => ({ ...window.__viewer.ctx.renderer.info.memory }));
const frames = () => page.evaluate(() => window.__viewer.ctx.renderer.info.render.frame);
/** Until every placed piece has its model (or 60 s). */
const loaded = () => page.waitForFunction(() => {
  const shown = [...window.__viewer.ctx.furniture.shown.values()];
  return shown.every((s) => s.materials.length > 0) && window.__viewer.ctx.furniture.fades.length === 0;
}, null, { timeout: 60_000 });

await page.goto(URL);
await page.waitForFunction(() => window.__viewer?.ctx?.model, null, { timeout: 180_000 });
const start = page.getByTestId('start-tour');
if (await start.isVisible().catch(() => false)) {
  await start.click({ timeout: 60_000 });
}
await page.waitForTimeout(4000);
const before = await memory();
await page.getByTestId('design-menu-toggle').click();

let first = null;
const pick = (i) => KITS[i % KITS.length];
for (let i = 0; i < SWITCHES; i++) {
  await page.click(`[data-style-id="${pick(i)}"]`);
  await loaded();
  await page.waitForTimeout(400);
  if (i === 0) first = await memory();
}
// The last switch shows the first kit again (20 is a multiple of the four
// kits, so switch 0 and switch 20 show the same one): show it now.
await page.click(`[data-style-id="${pick(0)}"]`);
await loaded();
await page.waitForTimeout(600);
const after = await memory();

await page.getByTestId('reset-all').click();
await page.waitForTimeout(1500);
const reset = await memory();

// Downloads still in flight when Reset was pressed land and are drawn (each
// wakes the view for 2 s): first wait for the view to go quiet, at most 30 s.
const quietFrom = Date.now();
for (let last = await frames(); Date.now() - quietFrom < 30_000; ) {
  await page.waitForTimeout(3000);
  const now = await frames();
  if (now === last) break;
  last = now;
}
const settledMs = Date.now() - quietFrom;

// Idle: then input, no input for 2 s, then 3 s more watching the frame counter.
await page.mouse.move(5, 5);
await page.waitForTimeout(2000);
const idleFrom = await frames();
await page.waitForTimeout(3000);
const idleTo = await frames();

const within = (a, b) => Math.abs(a - b) <= 0.1 * Math.max(a, 1);
const result = {
  before, first, after, reset,
  sameKit: within(first.geometries, after.geometries) && within(first.textures, after.textures),
  backAfterReset: within(before.geometries, reset.geometries) && within(before.textures, reset.textures),
  settledMs,
  framesWhileIdle: idleTo - idleFrom,
  errors,
};
console.log(JSON.stringify(result, null, 1));
await browser.close();
process.exit(result.sameKit && result.backAfterReset && result.framesWhileIdle === 0 && !errors.length ? 0 : 1);
