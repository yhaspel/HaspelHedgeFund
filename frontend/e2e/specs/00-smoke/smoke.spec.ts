import { test, expect, UNAUTHENTICATED } from '../../fixtures';
import { LANE, FROZEN_TIME } from '../../setup/env';

/** WS-0 · Smoke & harness self-test. Proves the harness itself works. */
test.describe('WS-0 · Smoke (unauthenticated)', () => {
  test.use({ storageState: UNAUTHENTICATED });

  test('S-01 unauthenticated / redirects to /login', async ({ page, login }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/login$/);
    await expect(login.submitButton()).toBeVisible();
  });
});

test.describe('WS-0 · Authed harness self-test', () => {
  test('S-02 authed session lands on Dashboard with app-shell + main landmark', async ({
    page,
    shell,
  }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/$/);
    await expect(shell.nav).toBeVisible();
    await expect(shell.main).toBeVisible();
  });

  test('S-03 mock router answers known endpoints with no unmatched routes', async ({
    page,
    shell,
    apiMock,
  }) => {
    test.skip(LANE === 'live', 'mock-only self-test');
    await page.goto('/');
    await expect(shell.nav).toBeVisible();
    // Let lazy/deferred widget fetches settle, then assert nothing fell through.
    await page.waitForTimeout(500);
    expect(apiMock.misses, `unmatched API routes: ${apiMock.misses.join(', ')}`).toEqual([]);
  });

  test('S-04 unmatched endpoint fails loudly (599)', async ({ page, apiMock }) => {
    test.skip(LANE === 'live', 'mock-only self-test');
    await page.goto('/');
    const status = await page.evaluate(async () => {
      const r = await fetch('/api/this-endpoint-does-not-exist/');
      return r.status;
    });
    expect(status).toBe(599);
    expect(apiMock.misses).toContain('GET /this-endpoint-does-not-exist/');
  });

  test('S-05 frozen clock is in effect', async ({ page }) => {
    await page.goto('/');
    const now = await page.evaluate(() => Date.now());
    expect(now).toBe(FROZEN_TIME.getTime());
  });

  test('S-06 @smoke health/boot against the live stack', async ({ page, shell }) => {
    test.skip(LANE !== 'live', 'Lane B only');
    const res = await page.request.get('/api/health/');
    expect(res.ok()).toBeTruthy();
    await page.goto('/');
    await expect(shell.nav).toBeVisible();
  });
});
