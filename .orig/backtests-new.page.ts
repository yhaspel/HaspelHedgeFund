import { Component, OnInit, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { ModelsStore } from '../../abstraction/models.store';
import { EstimateResponse } from '../../core/models/backtest.model';
import { ModelPanelComponent } from '../shared/model-panel.component';

@Component({
  selector: 'hf-backtests-new',
  standalone: true,
  imports: [FormsModule, RouterLink, DecimalPipe, ModelPanelComponent],
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

        <div class="grid grid-cols-3 gap-4">
          <label class="text-sm font-medium block">
            Commission (bps)
            <input type="number" name="comm" [(ngModel)]="commissionBps" min="0" class="mt-1 w-full border rounded px-3 py-2" />
          </label>
          <label class="text-sm font-medium block">
            Spread (bps)
            <input type="number" name="spr" [(ngModel)]="spreadBps" min="0" class="mt-1 w-full border rounded px-3 py-2" />
          </label>
          <label class="text-sm font-medium block">
            Max budget (USD)
            <input type="number" name="bud" [(ngModel)]="maxBudgetUsd" min="0.5" step="0.5" class="mt-1 w-full border rounded px-3 py-2" />
            <p class="text-xs text-gray-500 mt-1">Run aborts if spend reaches this cap.</p>
          </label>
        </div>

        <hf-model-panel
          [agents]="councilAgents"
          [multiplier]="rebalanceCountEstimate()"
          [(overrides)]="overrides"
        />

        @if (error()) {
          <p class="text-red-600 text-sm">{{ error() }}</p>
        }

        @if (!estimate()) {
          <button type="button" (click)="estimateCost()" [disabled]="estimating()"
            class="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50">
            {{ estimating() ? 'Estimating…' : 'Estimate cost' }}
          </button>
        } @else {
          <div class="border rounded p-4 space-y-2"
            [class.bg-red-50]="estimate()!.exceeds_budget"
            [class.border-red-300]="estimate()!.exceeds_budget"
            [class.bg-green-50]="!estimate()!.exceeds_budget"
            [class.border-green-300]="!estimate()!.exceeds_budget">
            <div class="flex justify-between items-baseline">
              <h3 class="text-sm font-semibold">Pre-flight estimate</h3>
              <button type="button" (click)="resetEstimate()" class="text-xs text-blue-600 hover:underline">
                Edit & re-estimate
              </button>
            </div>
            <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
              <div>Estimated cost:
                <span class="font-mono font-semibold"
                  [class.text-red-700]="estimate()!.exceeds_budget">
                  \${{ estimate()!.est_total_usd | number: '1.2-2' }}
                </span>
                <span class="text-gray-500"> / cap \${{ estimate()!.budget_cap_usd | number: '1.2-2' }}</span>
              </div>
              <div>LLM calls: <span class="font-mono">{{ estimate()!.n_llm_calls | number }}</span></div>
              <div>Rebalance days: <span class="font-mono">{{ estimate()!.n_rebalance_days }}</span></div>
              <div>Est. wall-time: <span class="font-mono">{{ estimate()!.est_minutes_optimistic | number: '1.0-1' }}–{{ estimate()!.est_minutes_upper | number: '1.0-1' }} min</span></div>
            </div>
            @if (estimate()!.exceeds_budget) {
              <p class="text-sm text-red-700 font-medium">
                ⚠ Estimated cost exceeds your max budget. The run will abort partway.
                Either raise the cap, shrink the universe/date range, or switch to a cheaper rebalance frequency.
              </p>
            }
            <details class="text-xs text-gray-600">
              <summary class="cursor-pointer">Per-agent breakdown</summary>
              <table class="mt-2 w-full font-mono">
                <thead><tr class="text-left text-gray-500">
                  <th>Agent</th><th>Model</th><th class="text-right">$/call</th><th class="text-right">Total</th>
                </tr></thead>
                <tbody>
                  @for (row of estimate()!.by_agent; track row.agent) {
                    <tr>
                      <td>{{ row.agent }}</td>
                      <td class="text-gray-500">{{ row.model }}</td>
                      <td class="text-right">{{ row.per_call_usd | number: '1.4-5' }}</td>
                      <td class="text-right">{{ row.total_usd | number: '1.2-4' }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </details>
            <button type="submit" [disabled]="submitting() || estimate()!.exceeds_budget"
              class="w-full rounded py-2 disabled:opacity-50"
              [class.bg-green-600]="!estimate()!.exceeds_budget"
              [class.bg-gray-400]="estimate()!.exceeds_budget"
              [class.text-white]="true">
              {{ submitting() ? 'Submitting…' : (estimate()!.exceeds_budget ? 'Over budget — raise cap to submit' : 'Confirm & start walk-forward') }}
            </button>
          </div>
        }
      </form>
    </div>
  `,
})
export class BacktestsNewPage implements OnInit {
  readonly store = inject(BacktestsStore);
  readonly modelsStore = inject(ModelsStore);
  private readonly router = inject(Router);

  readonly councilAgents = [
    'buffett', 'munger', 'graham', 'wood', 'druckenmiller',
    'fundamentals', 'technicals', 'valuation', 'sentiment',
    'macro', 'news_digest', 'risk_manager', 'portfolio_manager',
  ];
  overrides = signal<Record<string, string>>({});

  rebalanceCountEstimate(): number {
    const start = new Date(this.startDate).getTime();
    const end = new Date(this.endDate).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 1;
    const days = (end - start) / 86400000;
    const stride = this.rebalance === 'daily' ? 1 : this.rebalance === 'weekly' ? 5 : 21;
    const nUniv = (this.parsedUniverse().length || 20);
    return Math.max(1, Math.round((days / stride) * nUniv));
  }

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
  maxBudgetUsd = 4.0;

  submitting = signal(false);
  estimating = signal(false);
  estimate = signal<EstimateResponse | null>(null);
  error = signal<string | null>(null);

  ngOnInit(): void {
    this.store.loadDefaultUniverse().subscribe();
    this.modelsStore.loadAll().subscribe();
  }

  private parsedUniverse(): string[] {
    const raw = this.universeStr
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    return raw.length ? raw : this.store.defaultUniverse();
  }

  resetEstimate(): void {
    this.estimate.set(null);
    this.error.set(null);
  }

  estimateCost(): void {
    this.estimating.set(true);
    this.error.set(null);
    this.store
      .estimate({
        universe: this.parsedUniverse(),
        start_date: this.startDate,
        end_date: this.endDate,
        rebalance_frequency: this.rebalance,
        max_budget_usd: this.maxBudgetUsd,
      })
      .subscribe({
        next: (est) => {
          this.estimating.set(false);
          this.estimate.set(est);
        },
        error: (e) => {
          this.estimating.set(false);
          const detail = e?.error;
          this.error.set(
            typeof detail === 'string'
              ? detail
              : detail?.detail || JSON.stringify(detail) || 'Failed to estimate',
          );
        },
      });
  }

  submit(): void {
    const est = this.estimate();
    if (!est) {
      // The form is in "estimate first" mode; ignore stray submits.
      return;
    }
    if (est.exceeds_budget) {
      this.error.set('Estimated cost exceeds the max budget. Raise the cap or shrink the run.');
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    this.store
      .create({
        name: this.name,
        universe: this.parsedUniverse(),
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
        max_budget_usd: this.maxBudgetUsd,
        model_overrides: this.overrides(),
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
