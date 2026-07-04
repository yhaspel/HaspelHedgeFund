import { test, expect } from '../../fixtures';

/**
 * P4-OFF WS-6.3 — offline-logic lane.
 *
 * Runs under the default (SW-blocked) Lane-A harness. Warm the IndexedDB cache
 * with an online visit, then abort every `/api/**` (incl. the health probe) to
 * simulate L2 (backend unreachable), reload, and assert the read-only offline
 * experience: the banner, cached data still rendering, and blocked writes.
 *
 * SW-shell boot-from-cache lives in the separate SW lane (playwright.offline.
 * config.ts) because Playwright route interception and service workers interact
 * badly.
 */
test.describe('WS-20 · Offline logic (L2)', () => {
  test('OFF-01 cached dashboard renders + L2 banner appears after the API drops', async ({
    page,
    dashboard,
  }) => {
    // 1. Warm the cache: an online visit fetches + caches the dashboard GETs.
    await dashboard.goto();
    await expect(dashboard.heading()).toBeVisible();
    await expect(dashboard.navHero()).toBeVisible();

    // 2. Drop the backend — abort every API call, including /api/health/.
    await page.route('**/api/**', (route) => route.abort());

    // 3. Reload: the shell boots, the interceptor replays cached GETs, and the
    //    failed health probe flips OfflineState to L2.
    await page.reload();

    // 4. The L2 banner shows and the cached data still renders (no error wall).
    const banner = page.getByTestId('offline-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/read-only/i);
    await expect(dashboard.navHero()).toBeVisible();
  });

  test('OFF-02 a write is blocked with a toast at L2', async ({ page, runs }) => {
    // Warm the new-run page online (default personas → submit enabled).
    await runs.gotoNew();
    await expect(runs.runCouncil()).toBeEnabled();

    await page.route('**/api/**', (route) => route.abort());
    await page.reload();
    await expect(page.getByTestId('offline-banner')).toBeVisible();

    // The write is blocked before the network and surfaces the offline toast.
    await runs.runCouncil().click();
    await expect(page.getByText(/Unavailable offline/i)).toBeVisible();
    // We never left the new-run page (no navigation to a run detail).
    await expect(page).not.toHaveURL(/\/runs\/\d+$/);
  });

  test('OFF-03 the Settings "simulate offline" toggle flips to L2 without the network', async ({
    page,
    settings,
  }) => {
    await settings.gotoDataNews();
    const toggle = page.getByTestId('offline-simulate-toggle');
    await expect(toggle).toBeVisible();
    await toggle.check();
    await expect(page.getByTestId('offline-banner')).toBeVisible();
    await toggle.uncheck();
  });
});
