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
  { path: '**', redirectTo: '' },
];
