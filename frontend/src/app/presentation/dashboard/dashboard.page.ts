import { Component, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';
import { RunsStore } from '../../abstraction/runs.store';

@Component({
  selector: 'hf-dashboard',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-8">
        <h1 class="text-2xl font-semibold">Dashboard</h1>
        <div class="flex items-center gap-4 text-sm text-gray-600">
          <span>{{ auth.user()?.email }}</span>
          <button (click)="auth.logout()" class="text-blue-600 hover:underline">
            Log out
          </button>
        </div>
      </header>

      <div class="mb-6">
        <a
          routerLink="/runs/new"
          class="inline-block bg-blue-600 text-white rounded px-4 py-2"
        >
          + New analysis
        </a>
      </div>

      <section class="bg-white rounded shadow">
        <h2 class="px-4 py-3 border-b font-semibold">Recent runs</h2>
        @if (runs.runs().length === 0) {
          <p class="p-4 text-gray-500 text-sm">
            No runs yet. Start your first analysis above.
          </p>
        } @else {
          <table class="w-full text-sm">
            <thead class="text-left text-gray-500">
              <tr>
                <th class="px-4 py-2">#</th>
                <th>Tickers</th>
                <th>As-of</th>
                <th>Status</th>
                <th class="text-right">Cost</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (r of runs.runs(); track r.id) {
                <tr class="border-t">
                  <td class="px-4 py-2">{{ r.id }}</td>
                  <td>{{ r.tickers.join(', ') }}</td>
                  <td>{{ r.as_of_date }}</td>
                  <td>{{ r.status }}</td>
                  <td class="text-right">\${{ (+r.total_cost_usd).toFixed(4) }}</td>
                  <td>
                    <a [routerLink]="['/runs', r.id]" class="text-blue-600">
                      view
                    </a>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    </div>
  `,
})
export class DashboardPage implements OnInit {
  readonly auth = inject(AuthStore);
  readonly runs = inject(RunsStore);

  ngOnInit(): void {
    this.runs.listRuns().subscribe();
  }
}
