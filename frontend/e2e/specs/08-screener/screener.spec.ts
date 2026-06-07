import { test, expect } from '../../fixtures';

/** WS-8 · Screener (`/screener`). */
test.describe('WS-8 · Screener', () => {
  test('SC-01 @mobile loads with presets; selecting one is accepted', async ({ screener }) => {
    await screener.goto();
    await expect(screener.heading()).toBeVisible();
    await expect(screener.presetChip('Momentum Stocks')).toBeVisible();
    await screener.presetChip('Momentum Stocks').click();
    await expect(screener.presetChip('Momentum Stocks')).toHaveClass(/active/);
  });

  test('SC-02 run a preset screen populates results', async ({ screener }) => {
    await screener.goto();
    await screener.presetChip('Momentum Stocks').click();
    await screener.runScreen().click();
    await expect(screener.resultsTable()).toBeVisible();
    await expect(screener.resultRows().first()).toBeVisible();
  });

  test('SC-09 empty results show the empty state, not an error', async ({ screener, apiMock }) => {
    apiMock.override('POST', '/screener/run/', { json: { rows: [], warnings: [], count: 0 } });
    await screener.goto();
    await screener.presetChip('Gap Up').click();
    await screener.runScreen().click();
    await expect(screener.emptyResults()).toBeVisible();
  });
});
