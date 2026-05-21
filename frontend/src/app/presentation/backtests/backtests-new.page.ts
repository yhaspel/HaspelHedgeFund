import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { ModelsStore } from '../../abstraction/models.store';
import { EstimateResponse } from '../../core/models/backtest.model';
import { ModelPanelComponent } from '../shared/model-panel.component';
import { GlossaryTermComponent } from '../shared/glossary-term.component';

@Component({
  selector: 'hf-backtests-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, DecimalPipe, ModelPanelComponent, AppShellComponent, GlossaryTermComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests', link:'/backtests'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Backtest setup</div>
          <h1 style="margin-top:6px">New walk-forward backtest</h1>
        </div>
      </div>

      <form (ngSubmit)="submit()" style="max-width:760px;display:flex;flex-direction:column;gap:18px">
        <section class="card">
          <div class="card-bd" style="display:flex;flex-direction:column;gap:14px">
            <div class="field">
              <label class="lbl">Name</label>
              <input class="input sans" name="name" [(ngModel)]="name" required />
            </div>
            <div class="field">
              <label class="lbl"><hf-term key="universe">Universe</hf-term> <span style="color:var(--text-3);text-transform:none;letter-spacing:0;font-weight:400">· comma-separated; blank uses default 20 quality names</span></label>
              <textarea class="input sans" name="universe" [(ngModel)]="universeStr" rows="3"
                placeholder="AAPL, MSFT, GOOGL, ..."
                style="height:auto;padding:10px;line-height:18px;font-family:var(--font-mono);font-size:13px"></textarea>
              <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:4px">
                Default universe: {{ store.defaultUniverse().join(', ') || '(loading)' }}
              </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
              <div class="field"><label class="lbl">Master start</label>
                <input class="input" type="date" name="start" [(ngModel)]="startDate" required /></div>
              <div class="field"><label class="lbl">Master end</label>
                <input class="input" type="date" name="end" [(ngModel)]="endDate" required /></div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><span class="title">Walk-forward</span></div>
          <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px">
            <div class="field"><label class="lbl">IS window (days)</label>
              <input class="input" type="number" name="is_w" [(ngModel)]="isWindow" min="126" /></div>
            <div class="field"><label class="lbl">OOS window (days)</label>
              <input class="input" type="number" name="oos_w" [(ngModel)]="oosWindow" min="21" /></div>
            <div class="field"><label class="lbl">Step (days)</label>
              <input class="input" type="number" name="step_w" [(ngModel)]="stepDays" min="21" /></div>
            <div class="field"><label class="lbl">Candidates / fold</label>
              <input class="input" type="number" name="ncand" [(ngModel)]="nCandidates" min="5" max="200" /></div>
            <div class="field"><label class="lbl">IS objective</label>
              <select class="input sans" name="obj" [(ngModel)]="objective">
                <option value="sharpe">Sharpe</option>
                <option value="sortino">Sortino</option>
                <option value="calmar">Calmar</option>
              </select>
              <p style="font-size:11px;color:var(--text-3);margin:4px 0 0">
                <hf-term key="sharpe">Sharpe</hf-term> / <hf-term key="sortino">Sortino</hf-term> · the score used to pick the best parameters in-sample.
              </p></div>
            <div class="field"><label class="lbl">Rebalance</label>
              <select class="input sans" name="rb" [(ngModel)]="rebalance">
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select></div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
            <div class="field"><label class="lbl">Starting cash (USD)</label>
              <input class="input" type="number" name="cash" [(ngModel)]="startingCash" min="1000" /></div>
            <div class="field"><label class="lbl">Baseline</label>
              <select class="input sans" name="bl" [(ngModel)]="baseline">
                <option value="universe_ew">Equal-weighted universe</option>
                <option value="spy">SPY</option>
              </select></div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px">
            <div class="field"><label class="lbl">Commission (bps)</label>
              <input class="input" type="number" name="comm" [(ngModel)]="commissionBps" min="0" /></div>
            <div class="field"><label class="lbl">Spread (bps)</label>
              <input class="input" type="number" name="spr" [(ngModel)]="spreadBps" min="0" /></div>
            <div class="field"><label class="lbl">Max budget (USD)</label>
              <input class="input" type="number" name="bud" [(ngModel)]="maxBudgetUsd" min="0.5" step="0.5" />
              <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:4px">Run aborts if spend reaches this cap.</div></div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd">
            <hf-model-panel [agents]="councilAgents" [multiplier]="rebalanceCountEstimate()" [(overrides)]="overrides" />
          </div>
        </section>

        @if (error()) {
          <p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>
        }

        @if (!estimate()) {
          <button type="button" class="btn primary" style="height:36px;justify-content:center"
            (click)="estimateCost()" [disabled]="estimating()">
            {{ estimating() ? 'Estimating…' : 'Estimate cost' }}
          </button>
        } @else {
          <section class="card"
            [style.borderColor]="estimate()!.exceeds_budget ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'"
            [style.background]="estimate()!.exceeds_budget ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'">
            <div class="card-bd" style="display:flex;flex-direction:column;gap:10px">
              <div style="display:flex;justify-content:space-between;align-items:baseline">
                <h3 style="font-size:14px;font-weight:600;margin:0">Pre-flight estimate</h3>
                <button type="button" (click)="resetEstimate()"
                  style="font-size:11.5px;color:var(--acc-info-fg);background:transparent;border:0;cursor:pointer;padding:0">
                  Edit &amp; re-estimate
                </button>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 24px;font-size:13px">
                <div>Estimated cost:
                  <span class="mono" style="font-weight:600"
                    [style.color]="estimate()!.exceeds_budget ? 'var(--acc-short-fg)' : 'var(--text)'">
                    $ {{ estimate()!.est_total_usd | number: '1.2-2' }}
                  </span>
                  <span style="color:var(--text-3)"> / cap $ {{ estimate()!.budget_cap_usd | number: '1.2-2' }}</span>
                </div>
                <div>LLM calls: <span class="mono">{{ estimate()!.n_llm_calls | number }}</span></div>
                <div>Rebalance days: <span class="mono">{{ estimate()!.n_rebalance_days }}</span></div>
                <div>Est. wall-time: <span class="mono">{{ estimate()!.est_minutes_optimistic | number: '1.0-1' }}–{{ estimate()!.est_minutes_upper | number: '1.0-1' }} min</span></div>
              </div>
              @if (estimate()!.exceeds_budget) {
                <p style="font-size:13px;color:var(--acc-short-fg);font-weight:500;margin:0">
                  ⚠ Estimated cost exceeds your max budget. The run will abort partway.
                  Either raise the cap, shrink the universe/date range, or switch to a cheaper rebalance frequency.
                </p>
              }
              <details style="font-size:11.5px;color:var(--text-2)">
                <summary style="cursor:pointer">Per-agent breakdown</summary>
                <table class="tbl" style="margin-top:6px">
                  <thead><tr>
                    <th>Agent</th><th>Model</th>
                    <th class="right">$/call</th><th class="right">Total</th>
                  </tr></thead>
                  <tbody>
                    @for (row of estimate()!.by_agent; track row.agent) {
                      <tr>
                        <td class="mono">{{ row.agent }}</td>
                        <td class="mono" style="color:var(--text-3)">{{ row.model }}</td>
                        <td class="num">{{ row.per_call_usd | number: '1.4-5' }}</td>
                        <td class="num">{{ row.total_usd | number: '1.2-4' }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </details>
              <button type="submit" class="btn"
                [class.primary]="!estimate()!.exceeds_budget"
                [disabled]="submitting() || estimate()!.exceeds_budget"
                style="height:36px;justify-content:center">
                {{ submitting() ? 'Submitting…' : (estimate()!.exceeds_budget ? 'Over budget — raise cap to submit' : 'Confirm & start walk-forward') }}
              </button>
            </div>
          </section>
        }
      </form>
    </hf-app-shell>
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
    const raw = this.universeStr.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    return raw.length ? raw : this.store.defaultUniverse();
  }

  resetEstimate(): void {
    this.estimate.set(null);
    this.error.set(null);
  }

  estimateCost(): void {
    this.estimating.set(true);
    this.error.set(null);
    this.store.estimate({
      universe: this.parsedUniverse(),
      start_date: this.startDate,
      end_date: this.endDate,
      rebalance_frequency: this.rebalance,
      max_budget_usd: this.maxBudgetUsd,
    }).subscribe({
      next: (est) => { this.estimating.set(false); this.estimate.set(est); },
      error: (e) => {
        this.estimating.set(false);
        const detail = e?.error;
        this.error.set(typeof detail === 'string' ? detail : detail?.detail || JSON.stringify(detail) || 'Failed to estimate');
      },
    });
  }

  submit(): void {
    const est = this.estimate();
    if (!est) return;
    if (est.exceeds_budget) {
      this.error.set('Estimated cost exceeds the max budget. Raise the cap or shrink the run.');
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    this.store.create({
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
    }).subscribe({
      next: (bt) => this.router.navigate(['/backtests', bt.id]),
      error: (e) => {
        this.submitting.set(false);
        const detail = e?.error;
        this.error.set(typeof detail === 'string' ? detail : detail?.detail || JSON.stringify(detail) || 'Failed to submit');
      },
    });
  }
}
