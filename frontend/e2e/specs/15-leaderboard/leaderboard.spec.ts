import { test, expect } from '../../fixtures';

/** WS-15 · Leaderboard (`/leaderboard`). */
test.describe('WS-15 · Leaderboard', () => {
  test('LB-01 renders ranked persona rows', async ({ leaderboard }) => {
    await leaderboard.goto();
    await expect(leaderboard.heading()).toBeVisible();
    await expect(leaderboard.topPersonas()).toBeVisible();
    await expect(leaderboard.personaRows().first()).toBeVisible();
  });

  test('LB-02 window filter + tab switch', async ({ leaderboard }) => {
    await leaderboard.goto();
    await leaderboard.windowSelect().selectOption({ index: 0 });
    await expect(leaderboard.topPersonas()).toBeVisible();
    await leaderboard.tab('Strategies').click();
    await expect(leaderboard.tab('Strategies')).toHaveAttribute('aria-selected', 'true');
    await expect(leaderboard.flavorBenchmarks()).toBeVisible();
  });

  test('LB-03 row drill-through opens the decisions panel', async ({ leaderboard }) => {
    await leaderboard.goto();
    await leaderboard.personaRows().first().click();
    await expect(leaderboard.drillPanel()).toBeVisible();
  });

  test('LB-04 empty state', async ({ leaderboard, apiMock }) => {
    apiMock.override('GET', '/leaderboard/agents/', { json: { rows: [] } });
    await leaderboard.goto();
    await expect(leaderboard.emptyAgents()).toBeVisible();
  });
});
