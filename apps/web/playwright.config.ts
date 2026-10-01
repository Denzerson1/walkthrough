import { defineConfig, devices } from '@playwright/test';

/**
 * Smoke tests on a desktop and an iPhone viewport, as the brief requires.
 *
 * WebGL in headless Chromium needs a software rasteriser on machines without
 * a usable GPU, which is the case both in CI and on this development box.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        launchOptions: {
          args: [
            '--use-gl=angle',
            '--use-angle=swiftshader',
            '--enable-unsafe-swiftshader',
            '--ignore-gpu-blocklist',
          ],
        },
      },
    },
    {
      // iPhone 13 maps to WebKit, so this exercises the engine iOS Safari
      // actually uses rather than Chromium at a narrow width.
      //
      // One retry: headless WebKit reclaims WebGL contexts slowly, so a run
      // that mounts the scene many times in sequence can transiently fail to
      // create one. This does not reproduce on a fresh page or on real iOS;
      // see docs/PROGRESS.md for what remains unverified on device.
      name: 'iphone',
      retries: 1,
      use: { ...devices['iPhone 13'] },
    },
  ],
});
