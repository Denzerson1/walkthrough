// The viewer's own pixels at given cameras, for `pipeline compare-browser`.
//
//   node scripts/browser-compare.mjs <viewer url> <cameras.json> <out dir>
//
// cameras.json: [{ "name", "position": [x, y, z], "yaw", "pitch", "fov" }]
// (degrees; lib/camera.ts: yaw 0 looks down -Z). Needs the dev server: the
// camera is set through the dev-only window.__viewer. Writes <name>.png of
// the canvas alone (no interface over it) and size.json with the canvas
// size the renders must match. Chrome, with the GPU, so Spark draws as a
// visitor's browser does.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { chromium } = createRequire(join(ROOT, 'apps', 'web', 'package.json'))('@playwright/test');

const [url, camerasFile, outDir] = process.argv.slice(2);
if (!url || !camerasFile || !outDir) {
  console.error('usage: node scripts/browser-compare.mjs <viewer url> <cameras.json> <out dir>');
  process.exit(2);
}
const cameras = JSON.parse(readFileSync(camerasFile, 'utf8'));
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-gpu-blocklist'] });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text().slice(0, 300)));
page.on('pageerror', (e) => errors.push(e.message));
await page.goto(url);
await page.waitForFunction(() => window.__viewer?.ctx?.splat, null, { timeout: 240_000 });
const start = page.getByTestId('start-tour');
if (await start.isVisible().catch(() => false)) await start.click({ timeout: 60_000 });
await page.waitForTimeout(4000);

let size = null;
for (const cam of cameras) {
  await page.evaluate((c) => {
    const s = window.__viewer.state;
    s.position = [...c.position];
    s.yaw = c.yaw;
    s.pitch = c.pitch;
    s.fov = c.fov;
    s.transition = null;
    window.__viewer.wake();
  }, cam);
  // Spark sorts on a worker; give it time to settle on the new view.
  await page.waitForTimeout(3000);
  const shot = await page.evaluate(() => {
    const { renderer, scene, camera } = window.__viewer.ctx;
    renderer.render(scene, camera);
    const canvas = renderer.domElement;
    return { data: canvas.toDataURL('image/png').split(',')[1], w: canvas.width, h: canvas.height,
             aspect: camera.aspect, fov: camera.fov };
  });
  size = { width: shot.w, height: shot.h, fov: shot.fov, aspect: shot.aspect };
  writeFileSync(`${outDir}/${cam.name}.png`, Buffer.from(shot.data, 'base64'));
}
writeFileSync(`${outDir}/size.json`, JSON.stringify({ ...size, errors }, null, 1));
await browser.close();
console.log(JSON.stringify({ shots: cameras.length, ...size, errors: errors.length }));
