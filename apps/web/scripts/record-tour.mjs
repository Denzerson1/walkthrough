// Film a walkthrough of a rebuilt flat, frame by frame, in the real viewer.
//
//   node scripts/record-tour.mjs <project> <tour.json> <frames dir> [fps] [width] [height]
//
// The tour is a list of keyframes: {"t": seconds, "at": [x, z], "yaw": deg,
// "pitch": deg, "daylight": "HH:MM" (optional)}. The camera glides through
// them on a smooth curve; each frame is a screenshot, so the video is as
// smooth as the frame rate asks, however slow the machine. Needs the dev
// servers running (the dev-only window.__viewer hook places the camera).
import { mkdirSync } from 'node:fs';
import { readFileSync } from 'node:fs';
import { chromium } from '@playwright/test';

const [project, tourPath, outDir, fpsArg = '30', w = '1920', h = '1080'] = process.argv.slice(2);
const fps = Number(fpsArg);
const keys = JSON.parse(readFileSync(tourPath, 'utf8'));
mkdirSync(outDir, { recursive: true });

const smooth = (t) => t * t * (3 - 2 * t);
const minutes = (s) => {
  const [hh, mm] = s.split(':').map(Number);
  return hh * 60 + mm;
};
// Catmull-Rom through the keyframe positions, so turns are rounded.
const spline = (p0, p1, p2, p3, t) =>
  0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t);
const angle = (a, b, t) => a + ((((b - a) % 360) + 540) % 360 - 180) * t;

function pose(time) {
  let i = 0;
  while (i < keys.length - 2 && keys[i + 1].t <= time) i++;
  const a = keys[i];
  const b = keys[Math.min(i + 1, keys.length - 1)];
  const t = b.t > a.t ? Math.min(Math.max((time - a.t) / (b.t - a.t), 0), 1) : 0;
  const before = keys[Math.max(i - 1, 0)];
  const after = keys[Math.min(i + 2, keys.length - 1)];
  const st = smooth(t);
  const x = spline(before.at[0], a.at[0], b.at[0], after.at[0], st);
  const z = spline(before.at[1], a.at[1], b.at[1], after.at[1], st);
  const daylight =
    a.daylight && b.daylight
      ? minutes(a.daylight) + (minutes(b.daylight) - minutes(a.daylight)) * st
      : undefined;
  return {
    x,
    z,
    yaw: angle(a.yaw, b.yaw, st),
    pitch: a.pitch + (b.pitch - a.pitch) * st,
    daylight,
  };
}

const browser = await chromium.launch({ channel: 'chrome', args: ['--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: Number(w), height: Number(h) } });
await page.goto(`http://localhost:5173/p/${project}`);
await page.waitForFunction(() => window.__viewer?.ctx?.model, null, { timeout: 180000 });
// The first-visit hint is for people, not for a film.
await page.addStyleTag({ content: '[data-testid="walk-hint"]{display:none!important}' });
await page.waitForTimeout(3000);

const total = keys[keys.length - 1].t;
const frames = Math.round(total * fps);
let lastDaylight;
for (let f = 0; f <= frames; f++) {
  const p = pose(f / fps);
  await page.evaluate(({ x, z, yaw, pitch }) => {
    const s = window.__viewer.state;
    s.position = [x, 1.6, z];
    s.yaw = yaw;
    s.pitch = pitch;
    s.transition = null;
  }, p);
  if (p.daylight !== undefined && Math.abs(p.daylight - (lastDaylight ?? -1)) >= 1) {
    // Move the real slider, so the film shows the control as well as the light.
    await page.$eval(
      '#daylight',
      (el, v) => {
        const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        set.call(el, String(Math.round(v / 5) * 5));
        el.dispatchEvent(new Event('input', { bubbles: true }));
      },
      p.daylight,
    );
    lastDaylight = p.daylight;
  }
  await page.waitForTimeout(f === 0 ? 500 : 40);
  await page.screenshot({ path: `${outDir}/frame-${String(f).padStart(5, '0')}.png` });
  if (f % 100 === 0) console.log(`frame ${f}/${frames}`);
}
await browser.close();
