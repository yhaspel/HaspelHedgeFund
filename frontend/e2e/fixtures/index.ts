/**
 * The custom `test` every spec imports. Extends `@playwright/test` with:
 *   - `apiMock`  — auto-installs the Lane-A `**\/api/**` router (mock lane only),
 *                  exposes `.override(method, path, resp)` for edge cases.
 *   - `clock`    — auto, freezes wall-clock time (§2.6).
 *   - Page Objects (`shell`, `login`, `dashboard`, …) — lazily constructed.
 *
 * Specs read like user stories; no raw locators or `page.route` in specs.
 */
import { test as base, expect } from '@playwright/test';
import { ApiMock } from './api-mock';
import { freezeClock } from '../utils/clock';
import { LANE } from '../setup/env';

import { AppShellPage } from '../pages/app-shell.page';
import { LoginPage } from '../pages/login.page';
import { SignupPage } from '../pages/signup.page';
import { DashboardPage } from '../pages/dashboard.page';
import { RunsPage } from '../pages/runs.page';
import { BacktestsPage } from '../pages/backtests.page';
import { StrategiesPage } from '../pages/strategies.page';
import { PortfolioPage } from '../pages/portfolio.page';
import { ScreenerPage } from '../pages/screener.page';
import { NewsPage } from '../pages/news.page';
import { WatchlistPage } from '../pages/watchlist.page';
import { GraphsPage } from '../pages/graphs.page';
import { BrokerPage } from '../pages/broker.page';
import { SettingsPage } from '../pages/settings.page';
import { SchedulesPage } from '../pages/schedules.page';
import { LeaderboardPage } from '../pages/leaderboard.page';
import { ProfilePage } from '../pages/profile.page';
import { InfoPage } from '../pages/info.page';
import { FundPage } from '../pages/fund.page';
import { CommandPalette } from '../components/command-palette';
import { Modal } from '../components/modal';

interface Fixtures {
  apiMock: ApiMock;
  shell: AppShellPage;
  login: LoginPage;
  signup: SignupPage;
  dashboard: DashboardPage;
  runs: RunsPage;
  backtests: BacktestsPage;
  strategies: StrategiesPage;
  portfolio: PortfolioPage;
  screener: ScreenerPage;
  news: NewsPage;
  watchlist: WatchlistPage;
  graphs: GraphsPage;
  broker: BrokerPage;
  settings: SettingsPage;
  schedules: SchedulesPage;
  leaderboard: LeaderboardPage;
  profile: ProfilePage;
  info: InfoPage;
  fund: FundPage;
  palette: CommandPalette;
  modal: Modal;
}

export const test = base.extend<Fixtures>({
  // Auto: freeze the clock for every test before it navigates.
  // eslint-disable-next-line no-empty-pattern
  page: async ({ page }, use) => {
    await freezeClock(page);
    await use(page);
  },

  // Auto: Lane-A network backbone. In the live lane this is a no-op shell so
  // specs can still call `.override(...)` without branching.
  apiMock: [
    async ({ page }, use) => {
      const mock = new ApiMock(page);
      if (LANE === 'mock') await mock.install();
      await use(mock);
    },
    { auto: true },
  ],

  shell: async ({ page }, use) => use(new AppShellPage(page)),
  login: async ({ page }, use) => use(new LoginPage(page)),
  signup: async ({ page }, use) => use(new SignupPage(page)),
  dashboard: async ({ page }, use) => use(new DashboardPage(page)),
  runs: async ({ page }, use) => use(new RunsPage(page)),
  backtests: async ({ page }, use) => use(new BacktestsPage(page)),
  strategies: async ({ page }, use) => use(new StrategiesPage(page)),
  portfolio: async ({ page }, use) => use(new PortfolioPage(page)),
  screener: async ({ page }, use) => use(new ScreenerPage(page)),
  news: async ({ page }, use) => use(new NewsPage(page)),
  watchlist: async ({ page }, use) => use(new WatchlistPage(page)),
  graphs: async ({ page }, use) => use(new GraphsPage(page)),
  broker: async ({ page }, use) => use(new BrokerPage(page)),
  settings: async ({ page }, use) => use(new SettingsPage(page)),
  schedules: async ({ page }, use) => use(new SchedulesPage(page)),
  leaderboard: async ({ page }, use) => use(new LeaderboardPage(page)),
  profile: async ({ page }, use) => use(new ProfilePage(page)),
  info: async ({ page }, use) => use(new InfoPage(page)),
  fund: async ({ page }, use) => use(new FundPage(page)),
  palette: async ({ page }, use) => use(new CommandPalette(page)),
  modal: async ({ page }, use) => use(new Modal(page)),
});

export { expect };
/** Empty storageState to force an unauthenticated start (auth/guard specs). */
export const UNAUTHENTICATED = { cookies: [], origins: [] };
