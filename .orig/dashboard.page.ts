import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';
import { MacroStore } from '../../abstraction/macro.store';
import { RunsStore } from '../../abstraction/runs.store';
import { StrategiesStore } from '../../abstraction/strategies.store';

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
          routerLink="/backtests/new"
          class="inline-block bg-emerald-600 text-white rounded px-4 py-2 mr-2"
        >
          + New backtest
        </a>
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
        <a
          routerLink="/strategies"
          class="inline-block bg-purple-600 text-white rounded px-4 py-2 ml-2"
          data-test="strategies-link"
        >
          Strategies
        </a>
        <a
          routerLink="/settings/models"
          class="inline-block bg-gray-200 text-gray-800 rounded px-4 py-2 ml-2"
          data-test="settings-link"
        >
          Settings
        </a>
      </div>

      <section class="bg-white rounded shadow mb-6 p-4">
        <div class="flex items-center justify-between mb-3">
          <h2 class="font-semibold">Current book</h2>
          @if (book(); as b) {
            <span class="text-xs text-gray-500">
              {{ b.strategy_name }} · {{ b.as_of_date }} ·
              gross {{ b.gross_pct }} · net {{ b.net_pct }}
            </span>
          }
        </div>
        @if (book(); as b) {
          <div class="grid grid-cols-2 gap-4">
            <div>
              <h3 class="text-xs font-semibold uppercase text-emerald-700 mb-1">
                Top longs
              </h3>
              <table class="w-full text-xs font-mono">
                @for (row of b.longs; track row.ticker) {
                  <tr>
                    <td>{{ row.ticker }}</td>
                    <td class="text-right">{{ row.weight.toFixed(2) }}%</td>
                  </tr>
                }
                @if (b.longs.length === 0) {
                  <tr><td class="text-gray-400">— none —</td></tr>
                }
              </table>
            </div>
            <div>
              <h3 class="text-xs font-semibold uppercase text-red-700 mb-1">
                Top shorts
              </h3>
              <table class="w-full text-xs font-mono">
                @for (row of b.shorts; track row.ticker) {
                  <tr>
                    <td>{{ row.ticker }}</td>
                    <td class="text-right">{{ row.weight.toFixed(2) }}%</td>
                  </tr>
                }
                @if (b.shorts.length === 0) {
                  <tr><td class="text-gray-400">— none —</td></tr>
                }
              </table>
            </div>
          </div>
        } @else {
          <p class="text-sm text-gray-500">
            No autonomous cycles yet.
            <a routerLink="/strategies/new" class="text-blue-600 hover:underline">
              Create a strategy
            </a>.
          </p>
        }
      </section>

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
  readonly strategies = inject(StrategiesStore);

  book = signal<{
    strategy_name: string;
    as_of_date: string;
    gross_pct: string;
    net_pct: string;
    longs: { ticker: string; weight: number }[];
    shorts: { ticker: string; weight: number }[];
  } | null>(null);

  ngOnInit(): void {
    this.runs.listRuns().subscribe();
    this.macro.loadSnapshot().subscribe({ error: () => {} });
    // Surface the latest "done" cycle of the user's most recent strategy.
    this.strategies.list().subscribe((ss) => {
      const recent = ss.find((s) => !!s.last_run_at) ?? ss[0];
      if (!recent) return;
      this.strategies.listCycles(recent.id).subscribe((cs) => {
        const done = cs.find((c) => c.status === 'done') ?? cs[0];
        if (!done) return;
        this.strategies.cycleDetail(recent.id, done.id).subscribe((d) => {
          const longs = Object.entries(d.target_weights)
            .filter(([, w]) => Number(w) > 0)
            .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
            .sort((a, b) => b.weight - a.weight).slice(0, 5);
          const shorts = Object.entries(d.target_weights)
            .filter(([, w]) => Number(w) < 0)
            .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
            .sort((a, b) => a.weight - b.weight).slice(0, 5);
          this.book.set({
            strategy_name: recent.name,
            as_of_date: d.as_of_date,
            gross_pct: d.gross_pct,
            net_pct: d.net_pct,
            longs, shorts,
          });
        });
      });
    });
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
