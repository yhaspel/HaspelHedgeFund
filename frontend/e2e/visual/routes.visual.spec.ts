import { test, expect } from '../fixtures';
import { NO_ANIMATIONS_CSS } from '../utils/clock';

/**
 * §5.1 — one screenshot per route (dark theme, desktop). Determinism: frozen
 * clock (auto fixture), animations disabled, dynamic regions masked so they
 * never cause false diffs. Baselines are the blessed Linux CI baselines under
 * visual/__screenshots__ (do not commit macOS captures). The light-theme +
 * viewport sets are added in M5.
 */
const ROUTES: { name: string; path: string }[] = [
  { name: 'login', path: '/login' },
  { name: 'signup', path: '/signup' },
  { name: 'fund-home', path: '/' },
  { name: 'dashboard', path: '/dashboard' },
  { name: 'runs-list', path: '/runs' },
  { name: 'runs-new', path: '/runs/new' },
  { name: 'run-detail', path: '/runs/371' },
  { name: 'backtests-list', path: '/backtests' },
  { name: 'backtests-new', path: '/backtests/new' },
  { name: 'backtest-detail', path: '/backtests/24' },
  { name: 'backtest-compare', path: '/backtests/24?compare=1' },
  { name: 'strategies-list', path: '/strategies' },
  { name: 'strategies-new', path: '/strategies/new' },
  { name: 'strategy-detail', path: '/strategies/48' },
  { name: 'autopilot', path: '/strategies/48/autopilot' },
  { name: 'fund', path: '/fund' },
  { name: 'portfolios', path: '/portfolios' },
  { name: 'portfolio', path: '/portfolio' },
  { name: 'screener', path: '/screener' },
  { name: 'news', path: '/news' },
  { name: 'watchlist', path: '/watchlist' },
  { name: 'graphs', path: '/graphs' },
  { name: 'graph-editor', path: '/graphs/7/edit' },
  { name: 'broker-accounts', path: '/broker-accounts' },
  { name: 'broker-connect', path: '/broker-accounts/connect' },
  { name: 'broker-pending', path: '/broker-accounts/pending' },
  { name: 'broker-overview', path: '/broker-accounts/13' },
  { name: 'settings-models', path: '/settings/models' },
  { name: 'settings-providers', path: '/settings/providers' },
  { name: 'settings-personas', path: '/settings/personas' },
  { name: 'settings-data-news', path: '/settings/data-news' },
  { name: 'settings-notifications', path: '/settings/notifications' },
  { name: 'schedules', path: '/schedules' },
  { name: 'leaderboard', path: '/leaderboard' },
  { name: 'profile', path: '/profile' },
  { name: 'questionnaire', path: '/profile/questionnaire' },
  { name: 'info-list', path: '/info' },
  { name: 'info-detail', path: '/info/welcome' },
];

for (const { name, path } of ROUTES) {
  test(`visual ${name}`, async ({ page }) => {
    await page.addStyleTag({ content: NO_ANIMATIONS_CSS });
    await page.goto(path);
    await page.waitForLoadState('networkidle').catch(() => {});
    await expect(page).toHaveScreenshot(`${name}.png`, {
      fullPage: true,
      // Mask non-deterministic regions (charts, sparklines, live regime bar,
      // chyron, relative timestamps).
      mask: [
        page.locator('canvas'),
        page.locator('hf-sparkline, hf-sparkbar'),
        page.locator('.regime-strip'),
        page.locator('hf-news-chyron'),
      ],
      maxDiffPixelRatio: 0.01,
    });
  });
}
