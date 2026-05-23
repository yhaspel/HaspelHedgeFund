import { Routes } from '@angular/router';
import { authGuard } from './core/auth/auth.guard';

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
    path: '',
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
    path: 'settings/models',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./presentation/settings/settings-models.page').then(
        (m) => m.SettingsModelsPage,
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
      import('./presentation/watchlist/watchlist.page').then(
        (m) => m.WatchlistPage,
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
  { path: '**', redirectTo: '' },
];
