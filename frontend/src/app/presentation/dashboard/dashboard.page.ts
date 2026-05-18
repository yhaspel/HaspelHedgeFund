import { Component, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';
import { MacroStore } from '../../abstraction/macro.store';
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

      <section class="bg-white rounded shadow mb-6 p-4">
        <div class="flex items-center justify-between mb-3">
          <h2 class="font-semibold">Macro regime</h2>
          @if (macro.snapshot(); as s) {
            <span class="text-xs text-gray-500">as of {{ s.as_of_date }}</span>
          }
        </div>
        @if (macro.snapshot(); as s) {
          <div class="flex flex-wrap gap-2 mb-3">
            <span class="px-2 py-1 rounded text-xs font-medium"
                  [class]="chipColor('growth', s.growth_quadrant)">
              growth: {{ s.growth_quadrant }}
            </span>
            <span class="px-2 py-1 rounded text-xs font-medium"
                  [class]="chipColor('inflation', s.inflation_regime)">
              inflation: {{ s.inflation_regime }}
            </span>
            <span class="px-2 py-1 rounded text-xs font-medium"
                  [class]="chipColor('curve', s.yield_curve_state)">
              yield curve: {{ s.yield_curve_state }}
            </span>
            <span class="px-2 py-1 rounded text-xs font-medium"
                  [class]="chipColor('policy', s.policy_stance)">
              policy: {{ s.policy_stance }}
            </span>
          </div>
          <p class="text-sm text-gray-700">{{ s.narrative }}</p>
        } @else {
          <p class="text-sm text-gray-500">Loading macro snapshot…</p>
        }
      </section>

      <div class="mb-6">
        <a
          routerLink="/backtests"
          class="inline-block bg-indigo-600 text-white rounded px-4 py-2 mr-2"
        >
          Backtests
        </a>
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
  readonly macro = inject(MacroStore);

  ngOnInit(): void {
    this.runs.listRuns().subscribe();
    this.macro.loadSnapshot().subscribe({ error: () => {} });
  }

  chipColor(kind: string, value: string): string {
    const positive = 'bg-green-100 text-green-800';
    const neutral = 'bg-gray-100 text-gray-800';
    const negative = 'bg-red-100 text-red-800';
    const warn = 'bg-yellow-100 text-yellow-800';
    const map: Record<string, Record<string, string>> = {
      growth: { expansion: positive, recovery: positive, slowdown: warn, recession: negative },
      inflation: { low: positive, moderate: neutral, high: warn, accelerating: negative },
      curve: { normal: positive, flat: warn, inverted: negative },
      policy: { easing: positive, neutral: neutral, tightening: warn },
    };
    return map[kind]?.[value] ?? neutral;
  }
}
