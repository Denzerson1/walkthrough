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
  // One retry everywhere. Headless WebGL here is SwiftShader at roughly one
  // frame per second, and it degrades further across a long run, so a
  // pixel-comparison test can time out waiting for a frame that a fresh run
  // produces fine. The flake is the renderer, not the app; a GPU runner does
  // not need this. See docs/PROGRESS.md.
  retries: 1,
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
      // WebKit additionally reclaims WebGL contexts slowly, so a long run can
      // transiently fail to create one. Not reproducible on a fresh page or,
      // as far as we know, on real iOS — which remains unverified on device.
      name: 'iphone',
      use: { ...devices['iPhone 13'] },
    },
  ],
});
