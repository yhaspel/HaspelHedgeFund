import { Component, inject } from '@angular/core';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-dashboard',
  standalone: true,
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-8">
        <h1 class="text-2xl font-semibold">Dashboard</h1>
        <div class="flex items-center gap-4 text-sm text-gray-600">
          <span>{{ auth.user()?.email }}</span>
          <button
            (click)="auth.logout()"
            class="text-blue-600 hover:underline"
          >
            Log out
          </button>
        </div>
      </header>
      <p class="text-gray-500">
        Empty dashboard. Phase 1 will add agent runs and portfolios here.
      </p>
    </div>
  `,
})
export class DashboardPage {
  readonly auth = inject(AuthStore);
}
