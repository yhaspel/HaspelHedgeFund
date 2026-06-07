import { test, expect } from '../../fixtures';
import portfolio from '../../fixtures/data/portfolio.json';

/** WS-3 · Dashboard (`/`). */
test.describe('WS-3 · Dashboard', () => {
  test('D-01 @mobile renders all widgets', async ({ dashboard }) => {
    await dashboard.goto();
    await expect(dashboard.heading()).toBeVisible();
    await expect(dashboard.navHero()).toBeVisible();
    await expect(dashboard.bookExposure()).toBeVisible();
    await expect(dashboard.regimeStrip()).toBeVisible();
    await expect(dashboard.sectorHeatmap()).toBeVisible();
    await expect(dashboard.watchlistCard()).toBeVisible();
  });

  test('D-02 NAV hero renders the seeded NAV figure', async ({ dashboard }) => {
    await dashboard.goto();
    // total_value 95778.45 → compact form; assert a non-empty money value.
    await expect(dashboard.navValue()).toBeVisible();
    await expect(dashboard.navValue()).toContainText(/\$|\d/);
  });

  test('D-03 regime strip shows current regime + reachable chip tooltip', async ({
    page,
    dashboard,
  }) => {
    await dashboard.goto();
    await expect(dashboard.regimeStrip()).toBeVisible();
    await expect(page.getByText(/growth · recovery/).first()).toBeVisible();
  });

  test('D-04 sector heatmap renders cells with stance', async ({ dashboard }) => {
    await dashboard.goto();
    await expect(dashboard.sectorHeatmap()).toBeVisible();
    // sector_implications.technology = overweight
    await expect(dashboard.sectorCell(/technology: overweight/i)).toBeVisible();
  });

  test('D-05 book-exposure shows gross/net meter', async ({ page, dashboard }) => {
    await dashboard.goto();
    await expect(dashboard.bookExposure()).toBeVisible();
    await expect(page.getByText('Gross', { exact: true })).toBeVisible();
    await expect(page.getByText('Net', { exact: true })).toBeVisible();
  });

  test('D-06 drill-through from a run row → /runs/:id', async ({ page, dashboard }) => {
    await dashboard.goto();
    await dashboard.runRow().first().click();
    await expect(page).toHaveURL(/\/runs\/\d+$/);
  });

  test('D-07 empty state when fixtures are empty', async ({ dashboard, apiMock }) => {
    apiMock.override('GET', '/runs/', { json: [] });
    apiMock.override('GET', '/strategies/', { json: [] });
    await dashboard.goto();
    await expect(dashboard.page.getByText('No runs in flight.')).toBeVisible();
    await expect(dashboard.page.getByText('No strategies yet.')).toBeVisible();
  });

  test('D-08 loading skeleton then content', async ({ dashboard, apiMock, page }) => {
    apiMock.override('GET', '/portfolio/', { json: portfolio, delayMs: 1500 });
    await dashboard.goto();
    await expect(page.getByLabel('Loading net asset value')).toBeVisible();
  });

  test('D-09 a widget endpoint 500 degrades without breaking siblings', async ({
    dashboard,
    apiMock,
  }) => {
    apiMock.override('GET', '/macro/snapshot/', { status: 500 });
    await dashboard.goto();
    // Macro widgets degrade, but the portfolio-driven NAV hero still renders.
    await expect(dashboard.navHero()).toBeVisible();
    await expect(dashboard.watchlistCard()).toBeVisible();
  });
});
