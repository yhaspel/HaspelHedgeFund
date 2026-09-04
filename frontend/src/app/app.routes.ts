import { Routes } from '@angular/router';
import { authGuard } from './core/auth/auth.guard';
import { environment } from '../environments/environment';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () =>
      import('./presentation/auth/login.page').then((m) => m.LoginPage),
  },
  {
    path: 'signup',
    loadComponent: () =>
      import('./presentation/auth/signup.page').then((m) => m.SignupPage),
  },
  {
    // P10 §C1 — fund-first: the landing page IS the fund (aggregate NAV,
    // 3 pod cards, equity history, composite). The old manual-book dashboard
    // is demoted to /dashboard (linked from the fund page's Manual book card).
    path: '',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/fund/fund-dashboard.page').then(
        (m) => m.FundDashboardPage,
      ),
  },
  {
    path: 'dashboard',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/dashboard/dashboard.page').then(
        (m) => m.DashboardPage,
      ),
  },
  {
    path: 'runs',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/runs/runs-list.page').then((m) => m.RunsListPage),
  },
  {
    path: 'runs/new',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/runs/runs-new.page').then((m) => m.RunsNewPage),
  },
  {
    path: 'runs/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/runs/runs-detail.page').then(
        (m) => m.RunsDetailPage,
      ),
  },
  {
    path: 'backtests',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/backtests/backtests-list.page').then(
        (m) => m.BacktestsListPage,
      ),
  },
  {
    path: 'backtests/new',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/backtests/backtests-new.page').then(
        (m) => m.BacktestsNewPage,
      ),
  },
  {
    path: 'backtests/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/backtests/backtests-detail.page').then(
        (m) => m.BacktestsDetailPage,
      ),
  },
  {
    path: 'backtests/:id/compare',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/backtests/backtests-compare.page').then(
        (m) => m.BacktestsComparePage,
      ),
  },
  {
    path: 'portfolios',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/portfolio/portfolios-hub.page').then(
        (m) => m.PortfoliosHubPage,
      ),
  },
  {
    path: 'portfolio',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/portfolio/portfolio.page').then(
        (m) => m.PortfolioPage,
      ),
  },
  {
    path: 'screener',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/screener/screener.page').then(
        (m) => m.ScreenerPage,
      ),
  },
  {
    path: 'news',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/news/news.page').then((m) => m.NewsPage),
  },
  {
    path: 'strategies',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/strategies/strategies-list.page').then(
        (m) => m.StrategiesListPage,
      ),
  },
  {
    path: 'strategies/new',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/strategies/strategies-new.page').then(
        (m) => m.StrategiesNewPage,
      ),
  },
  {
    path: 'strategies/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/strategies/strategies-detail.page').then(
        (m) => m.StrategiesDetailPage,
      ),
  },
  {
    // P14 — the per-strategy autopilot panel lives INSIDE the Fund tab now
    // (/fund/strategies/:id); the old address redirects so deep links survive.
    path: 'strategies/:id/autopilot',
    redirectTo: 'fund/strategies/:id',
  },
  {
    // P7 / P14 — the autonomous fund dashboard: the shared account, member
    // sleeves, roster + allocations, reset, aggregate + correlation.
    path: 'fund',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/fund/fund-dashboard.page').then(
        (m) => m.FundDashboardPage,
      ),
  },
  {
    // P14 — one member's panel (validation gate, enable, schedule, guardrails,
    // run history, sleeve book) under the Fund tab.
    path: 'fund/strategies/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/strategies/autopilot-panel.page').then(
        (m) => m.AutopilotPanelPage,
      ),
  },
  {
    path: 'graphs',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/graph-editor/graphs-list.page').then(
        (m) => m.GraphsListPage,
      ),
  },
  {
    path: 'graphs/:id/edit',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/graph-editor/graph-editor.page').then(
        (m) => m.GraphEditorPage,
      ),
  },
  // P10 §D5: Settings opens on General (Data & News); Models/Providers/
  // Personas live under the "Models & advanced" group.
  { path: 'settings', pathMatch: 'full', redirectTo: 'settings/data-news' },
  {
    path: 'settings/models',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/settings/settings-models.page').then(
        (m) => m.SettingsModelsPage,
      ),
  },
  {
    path: 'settings/providers',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/settings/settings-providers.page').then(
        (m) => m.SettingsProvidersPage,
      ),
  },
  {
    path: 'settings/personas',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/settings/settings-personas.page').then(
        (m) => m.SettingsPersonasPage,
      ),
  },
  {
    path: 'settings/costs',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/settings/settings-costs.page').then(
        (m) => m.SettingsCostsPage,
      ),
  },
  {
    path: 'settings/data-news',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/settings/settings-data-news.page').then(
        (m) => m.SettingsDataNewsPage,
      ),
  },
  {
    path: 'settings/notifications',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/settings/settings-notifications.page').then(
        (m) => m.SettingsNotificationsPage,
      ),
  },
  {
    path: 'schedules',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/schedules/schedules.page').then(
        (m) => m.SchedulesPage,
      ),
  },
  {
    path: 'leaderboard',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/leaderboard/leaderboard.page').then(
        (m) => m.LeaderboardPage,
      ),
  },
  {
    path: 'profile',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/profile/profile.page').then((m) => m.ProfilePage),
  },
  {
    path: 'profile/questionnaire',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/profile/questionnaire.page').then(
        (m) => m.QuestionnairePage,
      ),
  },
  {
    path: 'watchlist',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/watchlist/watchlists-manager.page').then(
        (m) => m.WatchlistsManagerPage,
      ),
  },
  {
    path: 'broker-accounts',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/broker-accounts/accounts.page').then(
        (m) => m.BrokerAccountsPage,
      ),
  },
  {
    path: 'broker-accounts/connect',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/broker-accounts/connect-wizard.page').then(
        (m) => m.BrokerConnectWizardPage,
      ),
  },
  {
    path: 'broker-accounts/pending',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/broker-accounts/pending-orders.page').then(
        (m) => m.PendingOrdersPage,
      ),
  },
  {
    path: 'broker-accounts/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/broker-accounts/account-overview.page').then(
        (m) => m.BrokerAccountOverviewPage,
      ),
  },
  {
    path: 'info',
    loadComponent: () =>
      import('./presentation/info/info-list.page').then((m) => m.InfoListPage),
  },
  {
    path: 'info/:slug',
    loadComponent: () =>
      import('./presentation/info/info-detail.page').then(
        (m) => m.InfoDetailPage,
      ),
  },
  // Phase 8 WS-18 — dev/`ct`-only component-in-harness route (excluded from the
  // production build via environment.ctHarness === false).
  ...(environment.ctHarness
    ? [
        {
          path: '__ct/:component',
          loadComponent: () =>
            import('./presentation/dev/ct-harness.page').then((m) => m.CtHarnessPage),
        },
      ]
    : []),
  { path: '**', redirectTo: '' },
];
