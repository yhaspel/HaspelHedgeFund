import { test, expect } from '../../fixtures';
import runDetail from '../../fixtures/data/run-detail.json';

/** WS-4 · Runs — write paths (new-run wizard + cancel). */
test.describe('WS-4 · Runs (write)', () => {
  test('R-04 new-run happy path → POST → redirect to detail', async ({ page, runs }) => {
    await runs.gotoNew();
    await expect(runs.newHeading()).toBeVisible();
    // ticker defaults to AAPL and the default personas are pre-selected.
    await expect(runs.runCouncil()).toBeEnabled();
    await runs.runCouncil().click();
    await expect(page).toHaveURL(/\/runs\/\d+$/);
  });

  test('R-05 new-run validation — blocked with no persona and no graph', async ({ page, runs }) => {
    await runs.gotoNew();
    // Wait for the app to hydrate (default personas selected → submit enabled);
    // under parallel load the cards can render before their listeners attach.
    await expect(runs.newHeading()).toBeVisible();
    await expect(runs.runCouncil()).toBeEnabled();
    // The persona checkbox is visually-hidden and toggles via its label. Click
    // each card at most once (by index) if it is checked — deterministic and
    // immune to the re-render lag that would re-toggle a re-queried ":checked".
    const cards = page.locator('hf-persona-card');
    const n = await cards.count();
    for (let i = 0; i < n; i++) {
      const cb = cards.nth(i).locator('input[type="checkbox"]');
      if (await cb.isChecked()) await cards.nth(i).click();
    }
    await expect(runs.runCouncil()).toBeDisabled({ timeout: 10_000 });
    await page.locator('hf-persona-card').first().click();
    await expect(runs.runCouncil()).toBeEnabled();
  });

  test('R-06 new-run from ticker context prefills the symbol', async ({ runs }) => {
    await runs.gotoNew('?ticker=NVDA');
    await expect(runs.tickerInput()).toHaveValue('NVDA');
  });

  test('R-10 cancel a running run → cancelled', async ({ runs, apiMock }) => {
    apiMock.override('GET', '/runs/:id/', { json: { ...runDetail, id: 371, status: 'running' } });
    await runs.gotoDetail(371);
    await expect(runs.stopAnalysis()).toBeVisible();
    const cancelReq = runs.page.waitForRequest(/\/runs\/\d+\/cancel\/$/);
    await runs.stopAnalysis().click();
    await cancelReq; // the cancel was dispatched (POST /runs/:id/cancel/)
  });
});
