import { test, expect } from '../../fixtures';

/** WS-2 · App shell & global navigation. Authed (default storageState). */
test.describe('WS-2 · App shell & global navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  // The sidebar destinations that actually exist (the plan's "New run" item is
  // a page CTA, not a sidebar entry — see dashboard).
  const NAV = [
    ['Dashboard', '/'],
    ['Portfolios', '/portfolios'],
    ['Watchlist', '/watchlist'],
    ['Broker accounts', '/broker-accounts'],
    ['Runs', '/runs'],
    ['Screener', '/screener'],
    ['Backtests', '/backtests'],
    ['Strategies', '/strategies'],
    ['Agent graphs', '/graphs'],
    ['News', '/news'],
    ['Schedules', '/schedules'],
    ['Leaderboard', '/leaderboard'],
    ['Autonomous Fund', '/fund'],
    ['Profile', '/profile'],
    ['Guides', '/info'],
    ['Settings', '/settings/models'],
  ] as const;

  test('N-01 sidebar navigates to every destination', async ({ page, shell }) => {
    // The sidebar persists across routes, so navigate sequentially without
    // resetting to '/' between items (faster + fewer requests under load).
    for (const [name, url] of NAV) {
      await shell.navigateVia(name);
      await expect(page).toHaveURL(new RegExp(url.replace(/\//g, '\\/') + '$'));
    }
  });

  test('N-02 active-route highlight tracks aria-current', async ({ page, shell }) => {
    await shell.navigateVia('Runs');
    await expect(shell.navLink('Runs')).toHaveAttribute('aria-current', 'page');
    await expect(shell.navLink('Backtests')).not.toHaveAttribute('aria-current', 'page');
  });

  test('N-03 skip-to-main-content link is reachable and moves focus to <main>', async ({
    page,
    shell,
  }) => {
    // Focus the skip link (it is the first tabbable element) and confirm it
    // reveals on focus, then that activating it moves focus into <main>.
    await shell.skipLink().focus();
    await expect(shell.skipLink()).toBeFocused();
    await expect(shell.skipLink()).toBeVisible();
    await shell.skipLink().press('Enter');
    await expect(page.locator('#main-content')).toBeFocused();
  });

  test('N-04 profile link routes to /profile', async ({ page, shell }) => {
    await shell.profileEmailLink().click();
    await expect(page).toHaveURL(/\/profile$/);
  });

  test('N-05 theme toggle flips + persists across reload', async ({ page, shell }) => {
    await expect.poll(() => shell.theme()).toBe('dark');
    await shell.toggleTheme();
    await expect.poll(() => shell.theme()).toBe('light');
    expect(await shell.storedTheme()).toBe('light');
    await page.reload();
    await expect.poll(() => shell.theme()).toBe('light'); // applied pre-paint
  });

  test('P-01 ⌘K opens the command palette with focused, labelled search', async ({
    page,
    shell,
    palette,
  }) => {
    await shell.openPaletteByShortcut();
    await expect(palette.dialog()).toBeVisible();
    await expect(palette.searchInput()).toBeFocused();
    await expect(palette.searchInput()).toHaveAttribute('aria-label', /Search runs/);
  });

  test('P-02 palette search filters + keyboard nav follows selection', async ({ shell, palette }) => {
    await shell.openPaletteByShortcut();
    await palette.type('a');
    await expect(palette.options().first()).toBeVisible();
    await palette.searchInput().press('ArrowDown');
    await expect(palette.activeOption()).toHaveCount(1);
  });

  test('P-03 palette Escape closes & restores focus to the trigger', async ({ page, shell, palette }) => {
    await shell.paletteTrigger().click();
    await expect(palette.dialog()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(palette.dialog()).toBeHidden();
    await expect(shell.paletteTrigger()).toBeFocused();
  });

  test('P-04 palette empty query shows the default search hint', async ({ page, shell, palette }) => {
    // The shipped palette shows a "type to search" hint for an empty query
    // (not aggregated results); typing then surfaces options (covered by P-02).
    await shell.openPaletteByShortcut();
    await expect(palette.dialog()).toBeVisible();
    await expect(page.getByText('Type to search runs, strategies, or backtests.')).toBeVisible();
  });
});
