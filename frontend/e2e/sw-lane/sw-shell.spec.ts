import { test, expect } from '@playwright/test';

/**
 * P4-OFF WS-6.3 — the app shell boots offline from the ngsw cache (R4).
 *
 * Uses the raw @playwright/test (no Lane-A fixtures / API mock): this lane runs
 * the real production build with a live service worker on :4173. It asserts only
 * shell-boot; deeper SW behaviour stays manual (see guides/offline-mode.md).
 */
test.describe('P4-OFF · SW-shell (boot offline)', () => {
  test('the shell boots from the SW cache after going offline', async ({ page, context }) => {
    // 1. First load online: ngsw registers, activates, and prefetches the shell.
    await page.goto('/');
    await expect(page.locator('app-root')).toBeAttached();
    await page.waitForFunction(
      async () => {
        const reg = await navigator.serviceWorker?.getRegistration();
        return !!reg?.active;
      },
      undefined,
      { timeout: 45_000 },
    );
    // A reload brings this client under the active SW's control.
    await page.reload();
    await page.waitForFunction(() => !!navigator.serviceWorker?.controller, undefined, {
      timeout: 15_000,
    });

    // 2. Go offline and reload — the shell must still boot from the SW cache.
    await context.setOffline(true);
    await page.reload();
    await expect(page.locator('app-root')).toBeAttached();
    // The router renders (an unauthenticated context lands on the login page).
    await expect(page.locator('body')).toContainText(/Log in|Haspel Hedge Fund/i, {
      timeout: 15_000,
    });
  });
});
