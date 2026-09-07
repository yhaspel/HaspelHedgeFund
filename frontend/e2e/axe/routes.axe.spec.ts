import { test, expect } from '../fixtures';
import { analyzeA11y } from '../utils/a11y';

/**
 * §5.2 — in-browser axe per route with real CSS, ZERO violations (full WCAG
 * 2.0/2.1 A+AA). The pre-existing DS debt the real-CSS lane first surfaced
 * (color-contrast, select-name, nested-interactive, label, link-in-text-block)
 * was remediated in the app, so there are no per-route baselines.
 */
const ROUTES: { path: string; known?: string[] }[] = [
  { path: '/login' },
  { path: '/signup' },
  { path: '/' },
  { path: '/dashboard' },
  { path: '/runs' },
  { path: '/runs/new' },
  { path: '/runs/371' },
  { path: '/backtests' },
  { path: '/backtests/new' },
  { path: '/backtests/24' },
  { path: '/backtests/24?compare=1' },
  { path: '/strategies' },
  { path: '/strategies/new' },
  { path: '/strategies/48' },
  { path: '/strategies/48/autopilot' },
  { path: '/fund' },
  { path: '/portfolios' },
  { path: '/portfolio' },
  { path: '/screener' },
  { path: '/news' },
  { path: '/watchlist' },
  { path: '/graphs' },
  { path: '/graphs/7/edit' },
  { path: '/broker-accounts' },
  { path: '/broker-accounts/connect' },
  { path: '/broker-accounts/pending' },
  { path: '/broker-accounts/13' },
  { path: '/settings/models' },
  { path: '/settings/providers' },
  { path: '/settings/personas' },
  { path: '/settings/data-news' },
  { path: '/settings/notifications' },
  { path: '/schedules' },
  { path: '/leaderboard' },
  { path: '/profile' },
  { path: '/profile/questionnaire' },
  { path: '/info' },
  { path: '/info/welcome' },
];

const EXCLUDE = ['.hf-term-trigger'];

for (const { path, known = [] } of ROUTES) {
  test(`axe ${path}`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState('networkidle').catch(() => {});
    const results = await analyzeA11y(page, EXCLUDE);
    const newViolations = results.violations.filter((v) => !known.includes(v.id));
    const summary = newViolations.map((v) => `${v.id}[${v.nodes.length}]`);
    expect(summary, `NEW axe violations on ${path}: ${summary.join(', ')}`).toEqual([]);
  });
}
