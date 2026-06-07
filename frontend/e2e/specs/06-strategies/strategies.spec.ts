import { test, expect } from '../../fixtures';

/** WS-6 · Strategies — the most interaction-dense surface (incl. ST-06 guardrail). */
test.describe('WS-6 · Strategies', () => {
  test('ST-01 list renders + row → detail; empty state', async ({ page, strategies, apiMock }) => {
    await strategies.goto();
    await expect(strategies.listHeading()).toBeVisible();
    await strategies.openLink().first().click();
    await expect(page).toHaveURL(/\/strategies\/\d+$/);

    apiMock.override('GET', '/strategies/', { json: [] });
    await strategies.goto();
    await expect(strategies.emptyList()).toBeVisible();
  });

  test('ST-02 new — persona grid toggles + selection count', async ({ strategies }) => {
    await strategies.gotoNew();
    await expect(strategies.newHeading()).toBeVisible();
    const cb = strategies.personaCheckboxes().first();
    await expect(cb).toBeChecked(); // all personas selected by default
    const before = await strategies.personaCount().textContent();
    await cb.uncheck({ force: true }); // visually-hidden; toggle directly
    await expect(cb).not.toBeChecked();
    await expect(strategies.personaCount()).not.toHaveText(before ?? '');
  });

  test('ST-03 new — kind + universe + gross/net inputs accept values', async ({ strategies }) => {
    await strategies.gotoNew();
    await strategies.kindSelect().selectOption({ index: 1 });
    await strategies.universeSelect().selectOption({ index: 1 });
    await strategies.grossInput().fill('1.2');
    await expect(strategies.grossInput()).toHaveValue('1.2');
  });

  test('ST-04 new — create → redirect to detail', async ({ page, strategies }) => {
    await strategies.gotoNew();
    await strategies.nameInput().fill('E2E Strategy');
    await strategies.universeSelect().selectOption({ index: 1 });
    await strategies.createButton().click();
    await expect(page).toHaveURL(/\/strategies\/\d+$/);
  });

  test('ST-05 detail — enroll opens modal', async ({ strategies }) => {
    await strategies.gotoDetail(48);
    await strategies.enterStrategy().first().click();
    await expect(strategies.enrollModalTitle()).toBeVisible();
  });

  test('ST-06 enroll guardrail — manual enrol blocked when no pick is approved', async ({
    page,
    strategies,
  }) => {
    await strategies.gotoDetail(48);
    await strategies.enterStrategy().first().click();
    await expect(strategies.enrollModalTitle()).toBeVisible();
    // Rows are default-approved → submit enabled.
    await expect(strategies.enrollSelected()).toBeEnabled();
    // Guardrail: un-approving every pick disables manual enrol (can't enter 0).
    const approvals = page.getByRole('checkbox', { name: /^Approve / });
    const n = await approvals.count();
    for (let i = 0; i < n; i++) await approvals.nth(i).uncheck();
    await expect(strategies.enrollSelected()).toBeDisabled();
    // Re-approving a pick lifts the guard.
    await strategies.approveCheckbox('CAT').check();
    await expect(strategies.enrollSelected()).toBeEnabled();
  });

  test('ST-07 enroll skip dismisses without enrolling', async ({ strategies }) => {
    await strategies.gotoDetail(48);
    await strategies.enterStrategy().first().click();
    await expect(strategies.enrollModalTitle()).toBeVisible();
    await strategies.enrollSkip().click();
    await expect(strategies.enrollModalTitle()).toBeHidden();
  });

  test('ST-08 detail — estimate cost modal opens + cancels', async ({ strategies }) => {
    await strategies.gotoDetail(48);
    await strategies.runCycleNow().click();
    await expect(strategies.estimateModalTitle()).toBeVisible();
    await strategies.cancelEstimate().click();
    await expect(strategies.estimateModalTitle()).toBeHidden();
  });

  test('ST-09 detail — confirm cycle dispatch', async ({ strategies }) => {
    await strategies.gotoDetail(48);
    await strategies.runCycleNow().click();
    await expect(strategies.estimateModalTitle()).toBeVisible();
    await strategies.confirmRun().click();
    await expect(strategies.notice()).toBeVisible();
  });

  test('ST-15 detail — regime-context widget renders', async ({ strategies }) => {
    await strategies.gotoDetail(48);
    await expect(strategies.regimeWidget()).toBeVisible();
  });

  test('ST-16 detail — auto-enroll toggle persists', async ({ strategies }) => {
    await strategies.gotoDetail(48);
    const toggle = strategies.autoEnrollToggle();
    const wasChecked = await toggle.isChecked();
    await toggle.click();
    await expect(toggle).toBeChecked({ checked: !wasChecked });
  });
});
