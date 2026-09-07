import { test, expect } from '../../fixtures';
import fundFixture from '../../fixtures/data/fund.json';
import autopilot from '../../fixtures/data/autopilot.json';

const clone = <T>(o: T): T => JSON.parse(JSON.stringify(o));

/** WS-19 · Autonomous Fund & Autopilot (paper-only). Endpoints hit the LLM/broker
 *  boundary → Lane A mocks them; Lane B uses the §3.7 stubs. */
test.describe('WS-19 · Autonomous Fund', () => {
  test('FN-01 @smoke fund dashboard renders aggregate + account cards', async ({ fund }) => {
    await fund.goto();
    await expect(fund.heading()).toBeVisible();
    await expect(fund.page.getByText(/Account NAV|Aggregate NAV/)).toBeVisible();
    await expect(fund.page.getByText('Fund DD halt')).toBeVisible();
    await expect(fund.disclaimer()).toBeVisible();
    // 3 account cards → their names link to the autopilot panel.
    await expect(fund.accountNameLink('Cross-Asset Trend (CTA-lite)')).toBeVisible();
  });

  test('FN-02 member card name deep-links to its panel under the Fund tab', async ({
    page,
    fund,
  }) => {
    await fund.goto();
    await fund.accountNameLink('Sector Rotation').click();
    await expect(page).toHaveURL(/\/fund\/strategies\/\d+$/);
  });

  test('FN-03 disabled-account badge self-explains via a tooltip', async ({ fund, apiMock }) => {
    const f = clone(fundFixture);
    f.members[0].is_enabled = false;
    f.members[0].state = 'paused';
    apiMock.override('GET', '/fund/', { json: f });
    await fund.goto();
    await expect(fund.disabledBadge()).toBeVisible();
    // The badge popover opens on hover/focus (a11y-rich, never a dead end).
    await fund.disabledBadge().focus();
    await expect(fund.page.getByRole('tooltip')).toBeVisible();
  });

  test('FN-04 "Run now" on an enabled account queues a cycle', async ({ fund }) => {
    await fund.goto();
    await fund.runNowButton().click();
    // Run now submits real orders to a live paper account, so it is gated on a
    // confirm dialog.
    await fund.confirmDialog().getByRole('button', { name: 'Run now and submit orders' }).click();
    await expect(fund.queuedNotice()).toBeVisible();
  });

  test('FN-05 fund kill switch — halt (type HALT) then clear', async ({ page, fund }) => {
    // Stateful mock: GET /fund/ reflects the halt flag set by the POST actions
    // (the store re-fetches after halt/resume rather than using the POST body).
    let halted = false;
    await page.route('**/api/fund/', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const f = clone(fundFixture);
      f.state = halted ? 'halted' : 'active';
      f.is_live = !halted;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(f) });
    });
    await page.route('**/api/fund/halt/', async (route) => {
      halted = true;
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    });
    await page.route('**/api/fund/resume/', async (route) => {
      halted = false;
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    });

    await fund.goto();
    await expect(fund.statePill()).toContainText('active');
    await fund.haltButton().click();
    // Confirm dialog requires typing the exact phrase HALT.
    await expect(fund.haltConfirm()).toBeDisabled();
    await fund.confirmInput().fill('HALT');
    await fund.haltConfirm().click();
    await expect(fund.statePill()).toContainText('halted');
    // Clearing the halt is an acknowledgment: confirm the re-arm dialog
    // (peaks rebase to current equity), then the fund reads active again.
    await fund.clearHaltButton().click();
    await fund.clearRearmConfirm().click();
    await expect(fund.statePill()).toContainText('active');
  });

  test('FN-06 not-live warning banner when no strategy is enabled', async ({ fund, apiMock }) => {
    const f = clone(fundFixture);
    f.is_live = false;
    f.members.forEach((a: { is_enabled: boolean }) => (a.is_enabled = false));
    apiMock.override('GET', '/fund/', { json: f });
    await fund.goto();
    await expect(fund.notLiveBanner()).toBeVisible();
  });

  test('FN-07 correlation matrix — insufficient-data message', async ({ fund }) => {
    await fund.goto();
    await expect(fund.correlationHeading()).toBeVisible();
    // The seeded fixture has < min_sample weekly returns.
    await expect(fund.page.getByText(/Insufficient data/)).toBeVisible();
  });

  test('FN-08 no-fund empty state', async ({ fund, apiMock }) => {
    // Send a literal JSON null (not {}) so the store's `r ?? null` yields null.
    apiMock.override('GET', '/fund/', { body: 'null', contentType: 'application/json' });
    await fund.goto();
    await expect(fund.emptyState()).toBeVisible();
  });

  test('FN-10 kill switch names the live member count, not a hard-coded 3', async ({
    fund,
    apiMock,
  }) => {
    await fund.goto();
    await expect(fund.haltButton()).toHaveText(/Halt all 3 strategies/);
    const f = clone(fundFixture);
    f.members = f.members.slice(0, 2);
    f.members_count = 2;
    apiMock.override('GET', '/fund/', { json: f });
    await fund.goto();
    await expect(fund.haltButton()).toHaveText(/Halt all 2 strategies/);
  });

  test('FN-11 Manage fund opens settings + roster with the members ticked at their shares', async ({
    fund,
  }) => {
    await fund.goto();
    await fund.manageButton().click();
    await expect(fund.settingsHeading()).toBeVisible();
    await expect(fund.accountSelect()).toHaveValue(/13/);
    await expect(fund.rosterHeading()).toBeVisible();
    await expect(fund.memberToggle('Sector Rotation')).toBeChecked();
    await expect(fund.memberToggle('Risk Parity')).not.toBeChecked();
    await expect(fund.memberShare('Sector Rotation')).toHaveValue('33.33');
    await expect(fund.allocationTotal()).toContainText('100%');
    // Nothing changed → nothing to save yet.
    await expect(fund.saveStrategiesButton()).toBeDisabled();
  });

  test('FN-12 adding a strategy re-levels the shares and saves the roster', async ({
    page,
    fund,
  }) => {
    await fund.goto();
    await fund.manageButton().click();
    await fund.memberToggle('Risk Parity').check();
    // 4 members → 25% each, total still 100%.
    await expect(fund.memberShare('Risk Parity')).toHaveValue('25');
    await expect(fund.allocationTotal()).toContainText('4 selected');
    await expect(fund.allocationTotal()).toContainText('100%');
    const req = page.waitForRequest(
      (r) => r.url().includes('/api/fund/members/') && r.method() === 'PUT',
    );
    await fund.saveStrategiesButton().click();
    const sent = (await req).postDataJSON() as { members: { allocation_pct: number }[] };
    expect(sent.members).toHaveLength(4);
    expect(sent.members.reduce((a, m) => a + m.allocation_pct, 0)).toBeCloseTo(100, 2);
    await expect(fund.membersSaved()).toBeVisible();
  });

  test('FN-13 a roster that does not total 100% cannot be saved', async ({ fund }) => {
    await fund.goto();
    await fund.manageButton().click();
    await fund.memberShare('Sector Rotation').fill('10');
    await expect(fund.allocationTotal()).toContainText('must total 100%');
    await expect(fund.saveStrategiesButton()).toBeDisabled();
    await fund.equalSplitButton().click();
    await expect(fund.allocationTotal()).toContainText('100%');
  });

  test('FN-14 unconfigured fund shows the set-up banner and opens the drawer by itself', async ({
    fund,
    apiMock,
  }) => {
    const f = {
      ...clone(fundFixture),
      is_configured: false,
      broker_account: null,
      is_live: false,
      reset: { ready: false, reason: 'no_account', positions: 0, inflight_orders: 0 },
    };
    apiMock.override('GET', '/fund/', { json: f });
    await fund.goto();
    await expect(fund.notConfiguredBanner()).toBeVisible();
    await expect(fund.settingsHeading()).toBeVisible();
    await expect(fund.resetButton()).toBeDisabled();
  });

  test('FN-15 Reset is gated on a flat account; Flatten queues the closes', async ({
    page,
    fund,
  }) => {
    await fund.goto();
    await fund.manageButton().click();
    // Fixture: 6 open positions → not ready.
    await expect(fund.resetButton()).toBeDisabled();
    await expect(fund.page.getByText(/still holds 6 positions/)).toBeVisible();
    const req = page.waitForRequest(/\/api\/fund\/flatten\/$/);
    await fund.flattenButton().click();
    await fund.confirmDialog().getByRole('button', { name: 'Flatten account' }).click();
    await req;
  });

  test('FN-16 legacy /strategies/:id/autopilot redirects under the Fund tab', async ({
    page,
    fund,
  }) => {
    await fund.gotoLegacyAutopilot(48);
    await expect(page).toHaveURL(/\/fund\/strategies\/48$/);
    await expect(fund.autopilotHeading()).toBeVisible();
  });

  test('FN-09 backtest CTA routes to New Backtest prefilled with the strategy', async ({
    page,
    fund,
  }) => {
    await fund.goto();
    await page
      .getByRole('link', { name: /Re-run|Run.*validation|validation backtest/i })
      .first()
      .click();
    await expect(page).toHaveURL(/\/backtests\/new/);
  });
});

/** Per-strategy Autopilot panel. */
test.describe('WS-19 · Autopilot panel', () => {
  test('AP-01 panel renders gate checklist + state', async ({ fund }) => {
    await fund.gotoAutopilot(48);
    await expect(fund.autopilotHeading()).toBeVisible();
    await expect(fund.validationGate()).toBeVisible();
  });

  test('AP-02 enable gated by validation', async ({ page, fund, apiMock }) => {
    // Gate NOT passed → Enable disabled + a "Run validation backtest" link.
    const notPassed = clone(autopilot);
    notPassed.autopilot.is_enabled = false;
    notPassed.autopilot.validation.passed = false;
    notPassed.autopilot.validation.checks.forEach((c: { ok: boolean }) => (c.ok = false));
    apiMock.override('GET', '/strategies/:id/autopilot/', { json: notPassed });
    await fund.gotoAutopilot(48);
    await expect(fund.enableButton()).toBeDisabled();
    await expect(fund.runValidationLink()).toBeVisible();

    // Gate passed → Enable becomes active (the gate is the exit-critical
    // behaviour; the enable POST itself is exercised via the symmetric disable
    // action in AP-03).
    const passed = clone(autopilot);
    passed.autopilot.is_enabled = false;
    passed.autopilot.validation.passed = true;
    apiMock.override('GET', '/strategies/:id/autopilot/', { json: passed });
    await fund.gotoAutopilot(48);
    await expect(fund.enableButton()).toBeEnabled();
    await expect(fund.runValidationLink()).toBeHidden();
  });

  test('AP-03 disable an enabled autopilot', async ({ page, fund }) => {
    // The seeded autopilot fixture is enabled → "Disable" is offered.
    await fund.gotoAutopilot(48);
    await expect(fund.disableButton()).toBeVisible();
    const req = page.waitForRequest(/\/autopilot\/disable\/$/);
    await fund.disableButton().click();
    // Disabling changes what a live paper account does, so it is gated on a
    // confirm dialog.
    await fund.confirmDialog().getByRole('button', { name: 'Disable autopilot' }).click();
    await req; // POST .../autopilot/disable/ dispatched
  });

  test('AP-08 run-history card renders', async ({ fund }) => {
    await fund.gotoAutopilot(48);
    await expect(fund.page.getByText(/Fired|run history|Run history/i).first()).toBeVisible();
  });

  test('AP-09 sleeve-book card renders (the member\'s slice of the shared account)', async ({
    fund,
  }) => {
    await fund.gotoAutopilot(48);
    await expect(fund.page.getByRole('heading', { name: 'Sleeve book' })).toBeVisible();
    await expect(fund.page.getByText(/Fund member/)).toBeVisible();
  });
});
