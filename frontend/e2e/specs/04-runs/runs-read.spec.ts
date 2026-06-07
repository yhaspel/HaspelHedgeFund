import { test, expect } from '../../fixtures';

/** WS-4 · Runs — read paths (list + detail tabs). Create/cancel live in M3. */
test.describe('WS-4 · Runs (read)', () => {
  test('R-01 @mobile runs list renders + row → detail', async ({ page, runs }) => {
    await runs.goto();
    await expect(runs.heading()).toBeVisible();
    await expect(runs.rows().first()).toBeVisible();
    await runs.rows().first().click();
    await expect(page).toHaveURL(/\/runs\/\d+$/);
    await expect(runs.detailHeading()).toBeVisible();
  });

  test('R-02 empty state → New run CTA', async ({ runs, apiMock }) => {
    apiMock.override('GET', '/runs/', { json: [] });
    await runs.goto();
    await expect(runs.emptyNoRuns()).toBeVisible();
    await expect(runs.page.getByRole('link', { name: 'Start your first run' })).toBeVisible();
  });

  test('R-03 status filter narrows the list', async ({ runs }) => {
    await runs.goto();
    await expect(runs.countHeading()).toContainText('All runs (6)');
    await runs.statusFilter('Running').click(); // all seeded runs are "done"
    await expect(runs.emptyNoMatch()).toBeVisible();
    await runs.statusFilter('Any').click();
    await expect(runs.rows().first()).toBeVisible();
  });

  test('R-07 run detail — 5 tabs selectable + arrow-key nav', async ({ runs }) => {
    await runs.gotoDetail(371);
    await expect(runs.detailHeading()).toBeVisible();
    for (const t of ['Decision', 'Council', 'Risk', 'CIO', 'Raw']) {
      await runs.tab(t).click();
      await expect(runs.tab(t)).toHaveAttribute('aria-selected', 'true');
    }
    // ←/→ roving tabindex moves selection.
    await runs.tab('Decision').click();
    await runs.tab('Decision').press('ArrowRight');
    await expect(runs.tab('Council')).toHaveAttribute('aria-selected', 'true');
    await runs.tab('Council').press('ArrowLeft');
    await expect(runs.tab('Decision')).toHaveAttribute('aria-selected', 'true');
  });

  test('R-08 deep-link ?tab=council lands on that tab', async ({ runs }) => {
    await runs.gotoDetail(371, 'council');
    await expect(runs.tab('Council')).toHaveAttribute('aria-selected', 'true');
    await expect(runs.tabPanel('council')).toBeVisible();
  });

  test('R-09 content renders per tab', async ({ runs }) => {
    await runs.gotoDetail(371);
    for (const id of ['decision', 'council', 'risk', 'cio', 'raw']) {
      const label = { decision: 'Decision', council: 'Council', risk: 'Risk', cio: 'CIO', raw: 'Raw' }[id]!;
      await runs.tab(label).click();
      await expect(runs.tabPanel(id)).toBeVisible();
    }
  });
});
