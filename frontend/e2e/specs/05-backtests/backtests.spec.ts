import { test, expect } from '../../fixtures';

/** WS-5 · Backtests (list / detail / new / compare). */
test.describe('WS-5 · Backtests', () => {
  test('BT-01 list renders + row → detail; empty state', async ({ page, backtests, apiMock }) => {
    await backtests.goto();
    await expect(backtests.listHeading()).toBeVisible();
    await backtests.rowLink(/Walk-forward validation/).first().click();
    await expect(page).toHaveURL(/\/backtests\/\d+$/);

    apiMock.override('GET', '/backtests/', { json: [] });
    await backtests.goto();
    await expect(backtests.emptyList()).toBeVisible();
  });

  test('BT-02 new — graph select populates', async ({ backtests }) => {
    await backtests.gotoNew();
    await expect(backtests.page.getByRole('heading', { name: 'New walk-forward backtest' })).toBeVisible();
    await expect(backtests.graphSelect()).toBeVisible();
    await expect(backtests.graphSelect().locator('option')).not.toHaveCount(0);
  });

  test('BT-03 new — submit a configured backtest', async ({ page, backtests }) => {
    await backtests.gotoNew();
    await backtests.graphSelect().selectOption({ index: 1 });
    await expect(backtests.runButton()).toBeVisible();
    await expect(backtests.runButton()).toBeEnabled();
    await backtests.runButton().click();
    // POST /backtests/ → queued; app navigates to the queued detail.
    await expect(page).toHaveURL(/\/backtests(\/\d+)?/);
  });

  test('BT-04 new — run button gated until a graph is chosen', async ({ backtests }) => {
    await backtests.gotoNew();
    await expect(backtests.runButton()).toHaveCount(0); // hidden with no graph
    await backtests.graphSelect().selectOption({ index: 1 });
    await expect(backtests.runButton()).toBeVisible();
  });

  test('BT-05 detail — equity-curve canvas renders', async ({ backtests }) => {
    await backtests.gotoDetail(24);
    await expect(backtests.equityCanvas()).toBeAttached();
  });

  test('BT-06 detail — summary metrics render', async ({ backtests }) => {
    await backtests.gotoDetail(24);
    await expect(backtests.metricLabel('Stitched OOS return')).toBeVisible();
    await expect(backtests.metricLabel('Max drawdown')).toBeVisible();
    await expect(backtests.page.getByText('18.50%')).toBeVisible();
  });

  test('BT-07 compare — overlay two backtests + metrics table', async ({ backtests }) => {
    await backtests.gotoCompare(24);
    await expect(backtests.page.getByRole('heading', { name: 'Compare backtests' })).toBeVisible();
    await backtests.compareBSelect().selectOption({ index: 1 });
    await expect(backtests.metricsTable()).toBeVisible();
    await expect(backtests.page.getByText('Mean OOS Sharpe')).toBeVisible();
  });

  test('BT-08 detail — 500 degrades gracefully', async ({ backtests, apiMock }) => {
    apiMock.override('GET', '/backtests/:id/', { status: 500 });
    await backtests.gotoDetail(24);
    // Page shell still renders the "Back to list" affordance, no crash.
    await expect(backtests.backToList()).toBeVisible();
  });
});
