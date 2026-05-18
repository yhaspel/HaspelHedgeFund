import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { BacktestsStore } from '../../abstraction/backtests.store';

@Component({
  selector: 'hf-backtests-new',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-8">
        <h1 class="text-2xl font-semibold">New walk-forward backtest</h1>
        <a routerLink="/backtests" class="text-blue-600 hover:underline">Backtests</a>
      </header>

      <form (ngSubmit)="submit()" class="bg-white p-6 rounded shadow w-full max-w-2xl space-y-4">
        <label class="block text-sm font-medium">
          Name
          <input name="name" [(ngModel)]="name" required class="mt-1 w-full border rounded px-3 py-2" />
        </label>

        <label class="block text-sm font-medium">
          Universe (comma-separated tickers; leave blank for default 20 quality names)
          <textarea
            name="universe"
            [(ngModel)]="universeStr"
            rows="3"
            class="mt-1 w-full border rounded px-3 py-2 font-mono text-sm"
            placeholder="AAPL, MSFT, GOOGL, ..."
          ></textarea>
          <p class="text-xs text-gray-500 mt-1">
            Default universe: {{ store.defaultUniverse().join(', ') || '(loading)' }}
          </p>
        </label>

        <div class="grid grid-cols-2 gap-4">
          <label class="text-sm font-medium block">
            Master start
            <input type="date" name="start" [(ngModel)]="startDate" required class="mt-1 w-full border rounded px-3 py-2" />
          </label>
          <label class="text-sm font-medium block">
            Master end
            <input type="date" name="end" [(ngModel)]="endDate" required class="mt-1 w-full border rounded px-3 py-2" />
          </label>
        </div>

        <fieldset class="border rounded p-4 space-y-3">
          <legend class="text-sm font-medium px-1">Walk-forward</legend>
          <div class="grid grid-cols-3 gap-3">
            <label class="text-sm">
              IS window (days)
              <input type="number" name="is_w" [(ngModel)]="isWindow" min="126" class="mt-1 w-full border rounded px-2 py-1" />
            </label>
            <label class="text-sm">
              OOS window (days)
              <input type="number" name="oos_w" [(ngModel)]="oosWindow" min="21" class="mt-1 w-full border rounded px-2 py-1" />
            </label>
            <label class="text-sm">
              Step (days)
              <input type="number" name="step_w" [(ngModel)]="stepDays" min="21" class="mt-1 w-full border rounded px-2 py-1" />
            </label>
            <label class="text-sm">
              Candidates / fold
              <input type="number" name="ncand" [(ngModel)]="nCandidates" min="5" max="200" class="mt-1 w-full border rounded px-2 py-1" />
            </label>
            <label class="text-sm">
              IS objective
              <select name="obj" [(ngModel)]="objective" class="mt-1 w-full border rounded px-2 py-1">
                <option value="sharpe">Sharpe</option>
                <option value="sortino">Sortino</option>
                <option value="calmar">Calmar</option>
              </select>
            </label>
            <label class="text-sm">
              Rebalance
              <select name="rb" [(ngModel)]="rebalance" class="mt-1 w-full border rounded px-2 py-1">
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </label>
          </div>
        </fieldset>

        <div class="grid grid-cols-2 gap-4">
          <label class="text-sm font-medium block">
            Starting cash (USD)
            <input type="number" name="cash" [(ngModel)]="startingCash" min="1000" class="mt-1 w-full border rounded px-3 py-2" />
          </label>
          <label class="text-sm font-medium block">
            Baseline
            <select name="bl" [(ngModel)]="baseline" class="mt-1 w-full border rounded px-3 py-2">
              <option value="universe_ew">Equal-weighted universe</option>
              <option value="spy">SPY</option>
            </select>
          </label>
        </div>

        <div class="grid grid-cols-2 gap-4">
          <label class="text-sm font-medium block">
            Commission (bps)
            <input type="number" name="comm" [(ngModel)]="commissionBps" min="0" class="mt-1 w-full border rounded px-3 py-2" />
          </label>
          <label class="text-sm font-medium block">
            Spread (bps)
            <input type="number" name="spr" [(ngModel)]="spreadBps" min="0" class="mt-1 w-full border rounded px-3 py-2" />
          </label>
        </div>

        @if (error()) {
          <p class="text-red-600 text-sm">{{ error() }}</p>
        }
        <button type="submit" [disabled]="submitting()" class="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50">
          {{ submitting() ? 'Submitting…' : 'Start walk-forward' }}
        </button>
      </form>
    </div>
  `,
})
export class BacktestsNewPage implements OnInit {
  readonly store = inject(BacktestsStore);
  private readonly router = inject(Router);

  name = 'WF ' + new Date().toISOString().slice(0, 10);
  universeStr = '';
  startDate = '2023-01-02';
  endDate = '2025-12-31';
  isWindow = 252;
  oosWindow = 63;
  stepDays = 63;
  nCandidates = 50;
  objective: 'sharpe' | 'sortino' | 'calmar' = 'sharpe';
  rebalance: 'daily' | 'weekly' | 'monthly' = 'weekly';
  startingCash = 100000;
  baseline: 'universe_ew' | 'spy' = 'universe_ew';
  commissionBps = 5;
  spreadBps = 5;

  submitting = signal(false);
  error = signal<string | null>(null);

  ngOnInit(): void {
    this.store.loadDefaultUniverse().subscribe();
  }

  submit(): void {
    this.submitting.set(true);
    this.error.set(null);
    const universe = this.universeStr
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    this.store
      .create({
        name: this.name,
        universe,
        start_date: this.startDate,
        end_date: this.endDate,
        starting_cash: this.startingCash,
        commission_bps: this.commissionBps,
        spread_bps: this.spreadBps,
        is_window_days: this.isWindow,
        oos_window_days: this.oosWindow,
        step_days: this.stepDays,
        n_candidates: this.nCandidates,
        is_objective: this.objective,
        rebalance_frequency: this.rebalance,
        baseline: this.baseline,
      })
      .subscribe({
        next: (bt) => this.router.navigate(['/backtests', bt.id]),
        error: (e) => {
          this.submitting.set(false);
          const detail = e?.error;
          this.error.set(
            typeof detail === 'string'
              ? detail
              : detail?.detail || JSON.stringify(detail) || 'Failed to submit',
          );
        },
      });
  }
}
