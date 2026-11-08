import { expect, test } from '@playwright/test';

const PROJECT = 'demo-01';

test.describe('landing', () => {
  test('offers the featured apartment', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toContainText(
      'Stand inside the flat',
    );
    await expect(
      page.getByRole('link', { name: /Walk through/ }),
    ).toBeVisible();
  });

  test('links through to the viewer', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: /Walk through/ }).click();
    await expect(page).toHaveURL(new RegExp(`/p/${PROJECT}`));
  });
});

test.describe('viewer', () => {
  test('renders the splat scene with no console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (m) => {
      if (m.type() !== 'error') return;
      // CI does not download the catalog meshes and floor textures (~150 MB
      // from third-party hosts, `pnpm seed:assets`), and the viewer draws a
      // correctly sized box or flat colour in their place. The browser still
      // logs each missing file. Only those are excused: any other 404, and
      // every script error, still fails the test.
      const missingAsset =
        m.text().startsWith('Failed to load resource') &&
        m.location().url.includes('/files/assets/');
      if (!missingAsset) errors.push(m.text());
    });
    page.on('pageerror', (e) => errors.push(e.message));

    await page.goto(`/p/${PROJECT}`);

    // A canvas means WebGL started; the room strip means the manifest loaded.
    await expect(page.locator('canvas')).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId('room-strip')).toBeVisible();

    // The loading overlay must clear, which only happens on SplatMesh onLoad.
    await expect(page.getByText(/Loading \d+%/)).toBeHidden({ timeout: 60_000 });

    expect(errors, `console errors: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('shows both rooms with their areas', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    const strip = page.getByTestId('room-strip');
    await expect(strip.getByRole('tab')).toHaveCount(7);
    await expect(strip).toContainText('Living room');
    await expect(strip).toContainText('Main bedroom');
    await expect(strip).toContainText('m²');
  });

  test('moves between rooms', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    const strip = page.getByTestId('room-strip');
    await strip.getByRole('tab', { name: /Main bedroom/ }).click();
    await expect(strip.getByRole('tab', { name: /Main bedroom/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test('draws the mini floor plan', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    await expect(page.locator('canvas')).toBeVisible({ timeout: 45_000 });
    const plan = page.getByTestId('floor-plan');
    await expect(plan).toBeVisible({ timeout: 45_000 });
    // One polygon per room.
    await expect(plan.locator('polygon')).toHaveCount(7);
  });

  test('applying a floor raises the compare and reset controls', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    // The capture opens as captured: furniture appears only when the user
    // adds it, so nothing is staged until they change something.
    await expect(page.getByTestId('room-strip')).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId('staged-badge')).toBeHidden();

    await page.getByTestId('design-menu-toggle').click();
    await page.getByTestId('design-tab-floor').click();
    await page.locator('[data-floor-id]').first().click();

    await expect(page.getByTestId('staged-badge')).toBeVisible();
    // The "Virtually staged" chip was removed by the owner; the controls that
    // get you back to the real capture are what has to survive.
    await expect(page.getByTestId('hold-original')).toBeVisible();
    await expect(page.getByTestId('reset-all')).toBeVisible();
  });

  test('reset returns to the original', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    await page.getByTestId('design-menu-toggle').click();
    await page.getByTestId('design-tab-floor').click();
    await page.locator('[data-floor-id]').first().click();
    await expect(page.getByTestId('staged-badge')).toBeVisible();

    await page.getByTestId('reset-all').click();
    await expect(page.getByTestId('staged-badge')).toBeHidden();
  });

  test("the menu's Reset all returns to the original, and Undo takes it back", async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    await page.getByTestId('design-menu-toggle').click();
    await expect(page.getByTestId('menu-reset')).toBeDisabled();
    await page.getByTestId('design-tab-floor').click();
    await page.locator('[data-floor-id]').first().click();
    await expect(page.getByTestId('staged-badge')).toBeVisible();

    // Undo while its toast is up. Its 5 s are less than a click's wait for
    // two still frames under software rendering (some 5.5 s each here), so
    // the click goes straight to the button.
    await page.getByTestId('menu-reset').click();
    await expect(page.getByTestId('toast')).toContainText('Back to the original');
    await page.getByTestId('toast-undo').dispatchEvent('click');
    await expect(page.getByTestId('staged-badge')).toBeVisible();

    // Left alone this time: the original, and nothing more to reset.
    await page.getByTestId('menu-reset').click();
    await expect(page.getByTestId('staged-badge')).toBeHidden();
    await expect(page.getByTestId('menu-reset')).toBeDisabled();
  });

  test('a floor goes in the room chosen, or in every room', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    const strip = page.getByTestId('room-strip');
    await expect(strip).toBeVisible({ timeout: 45_000 });
    const ids = (await strip.locator('[data-room-id]').evaluateAll((all) =>
      all.map((room) => room.getAttribute('data-room-id') ?? ''),
    ));
    const changed = (id: string) =>
      strip.locator(`[data-room-id="${id}"] [aria-label="Changed from the original"]`);
    const [first, last] = [ids[0], ids[ids.length - 1]];

    // One room, wherever you stand: the last one, not the one you are in.
    await page.getByTestId('design-menu-toggle').click();
    await page.getByTestId('design-tab-floor').click();
    await page.locator(`[data-scope="${last}"]`).click();
    await page.locator('[data-floor-id]').first().click();
    await page.getByTestId('design-menu-toggle').click(); // a phone shows the rooms again
    await expect(changed(last)).toBeVisible();
    await expect(changed(first)).toBeHidden();

    // The whole flat: every room, and the floor marked as the one laid.
    await page.getByTestId('design-menu-toggle').click();
    await page.locator('[data-scope="all"]').click();
    const floor = page.locator('[data-floor-id]').nth(1);
    await floor.click();
    await expect(floor).toHaveAttribute('aria-pressed', 'true');
    await page.getByTestId('design-menu-toggle').click();
    for (const id of ids) await expect(changed(id)).toBeVisible();
  });

  test("a floor laid in one room leaves the other rooms' captured floors", async ({ page }) => {
    // Dev server only: the scene graph is read through window.__viewer.
    await page.goto(`/p/${PROJECT}`);
    const strip = page.getByTestId('room-strip');
    await expect(strip).toBeVisible({ timeout: 45_000 });
    await page.waitForFunction(() => {
      const ctx = (window as unknown as { __viewer?: { ctx?: { floorSplats?: Map<string, unknown> } } })
        .__viewer?.ctx;
      return (ctx?.floorSplats?.size ?? 0) > 1;
    });
    const drawn = () =>
      page.evaluate(() => {
        const ctx = (window as unknown as {
          __viewer: { ctx: { scene: unknown; floorSplats: Map<string, { parent: unknown }> } };
        }).__viewer.ctx;
        return [...ctx.floorSplats].filter(([, m]) => m.parent === ctx.scene).map(([id]) => id).sort();
      });
    const all = await drawn();
    const last = all[all.length - 1];

    await page.getByTestId('design-menu-toggle').click();
    await page.getByTestId('design-tab-floor').click();
    await page.locator(`[data-scope="${last}"]`).click();
    await page.locator('[data-floor-id]').first().click();
    await expect.poll(drawn).toEqual(all.filter((id) => id !== last));

    await page.getByTestId('reset-all').click();
    await expect.poll(drawn).toEqual(all);
  });

  /**
   * Stub the clipboard rather than granting permissions: WebKit does not
   * support the clipboard-read permission in Playwright, and this records
   * exactly what the app tried to copy on every engine.
   */
  async function captureShareUrl(page: import('@playwright/test').Page): Promise<string> {
    await page.addInitScript(() => {
      (window as unknown as { __copied?: string }).__copied = undefined;
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {
          writeText: (text: string) => {
            (window as unknown as { __copied?: string }).__copied = text;
            return Promise.resolve();
          },
        },
      });
    });
    await page.goto(`/p/${PROJECT}`);
    await page.getByTestId('design-menu-toggle').click();
    await page.getByTestId('design-tab-floor').click();
    await page.locator('[data-floor-id]').first().click();
    await page.getByTestId('share').click();
    await expect(page.getByTestId('toast')).toContainText('Link copied');
    return page.evaluate(() => (window as unknown as { __copied: string }).__copied);
  }

  test('share produces a link carrying the viewer state', async ({ page }) => {
    const url = await captureShareUrl(page);
    expect(url).toContain(`/p/${PROJECT}`);
    expect(url).toContain('?s=');
  });

  test('a shared link restores the staged state', async ({ page }) => {
    const url = await captureShareUrl(page);
    await page.goto(url);
    await expect(page.getByTestId('staged-badge')).toBeVisible();
  });

  test('assistant reports clearly when it is not configured', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    await page.getByTestId('tool-assistant').click();
    await page.getByLabel('Message the assistant').fill('make it warmer');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.getByRole('alert')).toContainText('ANTHROPIC_API_KEY');
  });

  test('an unknown apartment explains itself', async ({ page }) => {
    await page.goto('/p/does-not-exist');
    await expect(page.getByRole('heading', { level: 1 })).toContainText(
      'This apartment is not here',
    );
  });
});

test.describe('editor', () => {
  test('is protected by the password', async ({ page }) => {
    await page.goto(`/edit/${PROJECT}`);
    await expect(page.getByLabel('Editor password')).toBeVisible();
  });

  test('rejects the wrong password', async ({ page }) => {
    await page.goto(`/edit/${PROJECT}`);
    await page.getByLabel('Editor password').fill('definitely-wrong');
    await page.getByRole('button', { name: 'Open editor' }).click();
    await expect(page.getByRole('alert')).toBeVisible();
  });
});
