// Download, load time, memory and frame times of a splat walkthrough, in
// Chrome with the GPU, on a desktop window and on a phone's viewport:
//
//   VIEWER_URL=http://localhost:5173/p/room3 node scripts/perf-check.mjs [out.json]
//
// "phone" is an iPhone 14 Pro Max viewport with touch and the CPU slowed 4x
// on this PC's GPU: it shows the layout and the CPU-bound work at phone
// scale, not a phone's GPU, memory or thermal limits. Only a physical phone
// measures those. Needs the dev server (it reads window.__viewer).

import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { chromium, devices } = createRequire(join(ROOT, 'apps', 'web', 'package.json'))('@playwright/test');
const URL = process.env.VIEWER_URL ?? 'http://localhost:5173/p/room3';
const out = process.argv[2];

async function measure(name, contextOptions, cpuRate) {
  const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-gpu-blocklist'] });
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  if (cpuRate > 1) {
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: cpuRate });
  }
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text().slice(0, 200)));
  const splatBytes = {};
  page.on('response', async (r) => {
    if (!/\.(spz|ply|png)(\?|$)/.test(r.url())) return;
    try {
      splatBytes[r.url().split('/').pop()] = (await r.body()).length;
    } catch {
      /* a response the page abandoned */
    }
  });
  const started = Date.now();
  await page.goto(URL);
  await page.waitForFunction(() => window.__viewer?.ctx?.splat, null, { timeout: 300_000 });
  const start = page.getByTestId('start-tour');
  if (await start.isVisible().catch(() => false)) await start.click({ timeout: 60_000 });
  // Loaded: every splat mesh initialised (Spark sets isInitialized).
  await page.waitForFunction(() => {
    const ctx = window.__viewer.ctx;
    const meshes = [ctx.splat, ...(ctx.floorSplats?.values() ?? [])];
    return meshes.every((m) => m?.isInitialized ?? m?.initialized ?? true);
  }, null, { timeout: 300_000 });
  const loadedMs = Date.now() - started;
  await page.waitForTimeout(2000);

  // Frame times while turning on the spot: every frame redraws and re-sorts.
  const frames = await page.evaluate(async () => {
    const v = window.__viewer;
    const times = [];
    let last = performance.now();
    const end = last + 6000;
    await new Promise((resolve) => {
      const tick = (now) => {
        times.push(now - last);
        last = now;
        v.state.yaw = (v.state.yaw + 0.6) % 360;
        v.wake();
        if (now < end) requestAnimationFrame(tick);
        else resolve();
      };
      requestAnimationFrame(tick);
    });
    times.shift();
    times.sort((a, b) => a - b);
    const pct = (p) => times[Math.min(times.length - 1, Math.floor(p * times.length))];
    return { frames: times.length, mean: times.reduce((a, b) => a + b, 0) / times.length,
             p50: pct(0.5), p95: pct(0.95), max: times[times.length - 1] };
  });
  const memory = await page.evaluate(() => ({
    jsHeapMB: performance.memory ? performance.memory.usedJSHeapSize / 1e6 : null,
    gl: window.__viewer.ctx.renderer.info.memory,
    canvas: [window.__viewer.ctx.renderer.domElement.width, window.__viewer.ctx.renderer.domElement.height],
  }));
  await browser.close();
  const downloadMB = Object.values(splatBytes).reduce((a, b) => a + b, 0) / 1e6;
  return { name, loadedMs, downloadMB: Math.round(downloadMB * 100) / 100, files: splatBytes,
           frames, memory, errors };
}

const results = [
  await measure('desktop 1280x800', { viewport: { width: 1280, height: 800 } }, 1),
  await measure('phone viewport (emulated: iPhone 14 Pro Max, CPU 4x slower, desktop GPU)',
                { ...devices['iPhone 14 Pro Max'], defaultBrowserType: undefined }, 4),
];
for (const r of results) {
  console.log(`${r.name}: loaded in ${(r.loadedMs / 1000).toFixed(1)} s, ${r.downloadMB} MB; ` +
    `frame mean ${r.frames.mean.toFixed(1)} ms, p95 ${r.frames.p95.toFixed(1)} ms; ` +
    `JS heap ${r.memory.jsHeapMB?.toFixed(0)} MB; errors ${r.errors.length}`);
}
if (out) writeFileSync(out, JSON.stringify(results, null, 1));
