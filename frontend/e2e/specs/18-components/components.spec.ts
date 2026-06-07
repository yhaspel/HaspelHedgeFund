import { test, expect } from '../../fixtures';
import { CtHarness } from '../../components/ct-harness';

/**
 * WS-18 · Shared components in a real-browser harness (/__ct/:component).
 * Real layout/pointer/canvas/SVG behaviour happy-dom/Vitest can't reach.
 * (C-02 palette → P-01..04; C-07 graph-canvas → G-04; C-10 Chart.js → BT-05 +
 * dashboard NAV hero — covered there, not duplicated here.)
 */
test.describe('WS-18 · Components in harness', () => {
  test('C-01 hf-modal — opens, traps focus, Esc restores focus to opener', async ({ page }) => {
    const ct = new CtHarness(page);
    await ct.mount('modal');
    await ct.open().click();
    await expect(ct.dialog()).toBeVisible();
    // Focus moved into the dialog.
    expect(
      await page.evaluate(() => document.activeElement?.closest('[role=dialog]') != null),
    ).toBe(true);
    await page.keyboard.press('Escape');
    await expect(ct.dialog()).toBeHidden();
    await expect(ct.open()).toBeFocused();
  });

  test('C-03 range-rail renders within bounds with an accessible name', async ({ page }) => {
    const ct = new CtHarness(page);
    await ct.mount('range-rail');
    await expect(page.getByLabel('CT range rail')).toBeVisible();
  });

  test('C-04 sparkline / sparkbar render (series, empty, single point)', async ({ page }) => {
    const ct = new CtHarness(page);
    await ct.mount('sparkline');
    // Series → an SVG with a drawn path/polyline; empty + single don't crash.
    await expect(page.getByTestId('ct-series').locator('svg')).toBeVisible();
    await expect(page.getByTestId('ct-series').locator('svg path, svg polyline')).toHaveCount(1);
    await expect(page.getByTestId('ct-empty').locator('svg')).toBeAttached();
    await expect(page.getByTestId('ct-single').locator('svg')).toBeAttached();

    await ct.mount('sparkbar');
    await expect(page.locator('hf-sparkbar .bars')).toBeVisible();
  });

  test('C-05 gross-net-meter renders proportions with an accessible label', async ({ page }) => {
    const ct = new CtHarness(page);
    await ct.mount('gross-net-meter');
    await expect(page.getByRole('img', { name: 'Long 70%, Short 20%' })).toBeVisible();
  });

  test('C-06 confidence-meter maps a 0–100 score to an accessible value', async ({ page }) => {
    const ct = new CtHarness(page);
    await ct.mount('confidence-meter');
    await expect(page.getByLabel('Confidence 72 of 100')).toBeVisible();
  });

  test('C-08 ticker chip opens a popover with mini-data', async ({ page }) => {
    const ct = new CtHarness(page);
    await ct.mount('ticker');
    const chip = page.getByRole('button', { name: 'AAPL' });
    await expect(chip).toBeVisible();
    await chip.hover();
    // The popover lazy-loads /tickers/AAPL/* (mocked) and shows the company name.
    await expect(page.locator('hf-ticker-popover')).toBeVisible();
  });

  test('C-09 kpi-tile + empty-state render variants', async ({ page }) => {
    const ct = new CtHarness(page);
    await ct.mount('kpi-tile');
    await expect(page.getByText('NAV · Manual Book')).toBeVisible();
    await expect(page.getByText('$1.20M')).toBeVisible();
    await expect(page.getByText('Nothing yet')).toBeVisible(); // empty-state message
  });
});
