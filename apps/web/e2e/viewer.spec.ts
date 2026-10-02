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
      if (m.type() === 'error') errors.push(m.text());
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
    await expect(strip.getByRole('tab')).toHaveCount(2);
    await expect(strip).toContainText('Living room');
    await expect(strip).toContainText('Bedroom');
    await expect(strip).toContainText('m²');
  });

  test('moves between rooms', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    const strip = page.getByTestId('room-strip');
    await strip.getByRole('tab', { name: /Bedroom/ }).click();
    await expect(strip.getByRole('tab', { name: /Bedroom/ })).toHaveAttribute(
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
    await expect(plan.locator('polygon')).toHaveCount(2);
  });

  test('applying a floor raises the Virtually staged badge', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    await expect(page.getByTestId('staged-badge')).toBeHidden();

    await page.getByTestId('tool-floors').click();
    await page.locator('[data-floor-id]').first().click();

    await expect(page.getByTestId('staged-badge')).toBeVisible();
    // Phones show the compact "Staged" wording.
    await expect(page.getByTestId('staged-badge')).toContainText(/Virtually staged|Staged/);
  });

  test('reset returns to the original', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    await page.getByTestId('tool-floors').click();
    await page.locator('[data-floor-id]').first().click();
    await expect(page.getByTestId('staged-badge')).toBeVisible();

    await page.getByTestId('reset-all').click();
    await expect(page.getByTestId('staged-badge')).toBeHidden();
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
    await page.getByTestId('tool-floors').click();
    await page.locator('[data-floor-id]').first().click();
    await page.getByTestId('share').click();
    await expect(page.getByTestId('share')).toContainText('Link copied');
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

test.describe('automatic layout', () => {
  test('furnishing a room places items and stages it', async ({ page }) => {
    await page.goto(`/p/${PROJECT}`);
    await expect(page.getByTestId('room-strip')).toBeVisible();

    await page.getByTestId('tool-furniture').click();
    await page.getByRole('button', { name: 'Furnish automatically' }).click();

    // The solver runs server-side, so this proves the round trip as well as
    // the staging rule.
    await expect(page.getByTestId('staged-badge')).toBeVisible({ timeout: 20_000 });
  });
});
