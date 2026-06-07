import { test, expect } from '../../fixtures';

/** WS-14 · Schedules (scheduled runs: list / create / toggle / edit cadence / delete / validation). */
test.describe('WS-14 · Schedules', () => {
  test('SH-01 schedules list renders from /scheduled-runs/', async ({ schedules }) => {
    await schedules.goto();
    await expect(schedules.heading()).toBeVisible();
    await expect(schedules.schedulesHeading()).toBeVisible();

    // Seed row from fixtures/data/scheduled-runs.json.
    const row = schedules.row('Daily watchlist check');
    await expect(row).toBeVisible();
    await expect(row).toContainText('My Watchlist');
    await expect(row).toContainText('At 09:25 AM, Monday through Friday');
    await expect(schedules.statusPill('Daily watchlist check')).toHaveText('Active');
  });

  test('SH-02 create schedule (graph + cadence) → POST → appears', async ({ page, schedules, apiMock }) => {
    await schedules.goto();
    await schedules.newScheduleButton().click();
    await expect(schedules.formHeading()).toBeVisible();

    await schedules.nameInput().fill('Morning macro sweep');
    // Cadence: pick a non-default frequency so the cron is rebuilt.
    await schedules.frequencySelect().selectOption('daily');
    // Graph select keyed by data-testid (default is "Council classic"; pick a real graph).
    await schedules.graphSelect().selectOption({ index: 1 });

    // After a successful POST the page reloads the list (GET /scheduled-runs/);
    // make the reload include the new row so it actually appears.
    apiMock.override('GET', '/scheduled-runs/', {
      json: [
        {
          id: 1,
          name: 'Daily watchlist check',
          watchlist: 1,
          watchlist_name: 'My Watchlist',
          model_preset: 'dev',
          cron_expression: '25 9 * * 1-5',
          cron_description: 'At 09:25 AM, Monday through Friday',
          timezone: 'America/New_York',
          is_market_aware: true,
          cost_ceiling_usd: '2.00',
          on_breach: 'degrade',
          notification_channel: null,
          is_active: true,
          next_run_at: '2026-06-08T13:25:00Z',
          last_run_at: '2026-06-05T13:25:03Z',
        },
        {
          id: 9900,
          name: 'Morning macro sweep',
          watchlist: 1,
          watchlist_name: 'My Watchlist',
          model_preset: 'frugal',
          cron_expression: '25 9 * * *',
          cron_description: 'Every day at 09:25 AM',
          timezone: 'America/New_York',
          is_market_aware: true,
          cost_ceiling_usd: null,
          on_breach: 'degrade',
          notification_channel: null,
          is_active: true,
          next_run_at: '2026-06-07T13:25:00Z',
          last_run_at: null,
        },
      ],
    });

    const postReq = page.waitForRequest(
      (r) => r.method() === 'POST' && /\/scheduled-runs\/$/.test(r.url()),
    );
    await schedules.createButton().click();
    const req = await postReq;
    // The create body carries the resolved cron + selected graph version.
    expect(req.postDataJSON()).toMatchObject({ name: 'Morning macro sweep', cron_expression: '25 9 * * *' });

    await expect(schedules.row('Morning macro sweep')).toBeVisible();
  });

  test('SH-03 toggle on/off persists via PATCH', async ({ page, schedules, apiMock }) => {
    await schedules.goto();
    await expect(schedules.statusPill('Daily watchlist check')).toHaveText('Active');

    // toggleActive → PATCH /scheduled-runs/:id/ { is_active: false } → reload().
    // Make the reload return the paused state so the UI reflects persistence.
    apiMock.override('GET', '/scheduled-runs/', {
      json: [
        {
          id: 1,
          name: 'Daily watchlist check',
          watchlist: 1,
          watchlist_name: 'My Watchlist',
          model_preset: 'dev',
          cron_expression: '25 9 * * 1-5',
          cron_description: 'At 09:25 AM, Monday through Friday',
          timezone: 'America/New_York',
          is_market_aware: true,
          cost_ceiling_usd: '2.00',
          on_breach: 'degrade',
          notification_channel: null,
          is_active: false,
          next_run_at: null,
          last_run_at: '2026-06-05T13:25:03Z',
        },
      ],
    });

    const patchReq = page.waitForRequest(
      (r) => r.method() === 'PATCH' && /\/scheduled-runs\/1\/$/.test(r.url()),
    );
    await schedules.toggleButton('Daily watchlist check').click();
    const req = await patchReq;
    expect(req.postDataJSON()).toMatchObject({ is_active: false });

    await expect(schedules.statusPill('Daily watchlist check')).toHaveText('Paused');
  });

  test('SH-04 edit cadence/graph in the form reflects in the cron preview', async ({ schedules }) => {
    // NOTE: this page has no per-row edit affordance — the only cadence/graph
    // editor is the create form. Verify that changing cadence + graph there
    // updates the resolved cron summary (closest honest "edit reflects").
    await schedules.goto();
    await schedules.newScheduleButton().click();
    await expect(schedules.formHeading()).toBeVisible();

    // Default frequency is "weekdays".
    await expect(schedules.cronSummary()).toContainText('Monday through Friday');

    await schedules.frequencySelect().selectOption('daily');
    await expect(schedules.cronSummary()).toContainText('Every day');

    // Graph select is editable and accepts a real graph version.
    await schedules.graphSelect().selectOption({ index: 1 });
    await expect(schedules.graphSelect()).not.toHaveValue('');
  });

  test('SH-05 delete (with confirm) → removed', async ({ page, schedules, apiMock }) => {
    await schedules.goto();
    await expect(schedules.row('Daily watchlist check')).toBeVisible();

    // After DELETE the page reloads; return an empty list so the row is gone.
    apiMock.override('GET', '/scheduled-runs/', { json: [] });

    await schedules.deleteButton('Daily watchlist check').click();
    // ConfirmService.ask() renders an hf-modal dialog titled with the schedule name.
    await expect(schedules.confirmDialog(/Delete schedule/)).toBeVisible();

    const deleteReq = page.waitForRequest(
      (r) => r.method() === 'DELETE' && /\/scheduled-runs\/1\/$/.test(r.url()),
    );
    await schedules.confirmDeleteButton().click();
    await deleteReq;

    await expect(schedules.emptyState()).toBeVisible();
    await expect(schedules.row('Daily watchlist check')).toHaveCount(0);
  });

  test('SH-06 validation: server rejection surfaces an error alert', async ({ schedules, apiMock }) => {
    // NOTE: the form has no client-side required-field gate — create always POSTs
    // (graph defaults to "Council classic"; cron always has a resolved value).
    // The honest "blocks create" behavior is the server 400 → role="alert" path.
    apiMock.override('POST', '/scheduled-runs/', {
      status: 400,
      json: { detail: 'A schedule needs a watchlist and a valid cadence.' },
    });

    await schedules.goto();
    await schedules.newScheduleButton().click();
    await schedules.createButton().click();

    await expect(schedules.alert()).toBeVisible();
    await expect(schedules.alert()).toContainText('A schedule needs a watchlist and a valid cadence.');
    // The form stays open (create did not succeed).
    await expect(schedules.formHeading()).toBeVisible();
  });
});
