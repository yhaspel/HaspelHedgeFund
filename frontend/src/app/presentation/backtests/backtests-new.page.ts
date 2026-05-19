import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { ModelsStore } from '../../abstraction/models.store';
import { EstimateResponse } from '../../core/models/backtest.model';

@Component({
  selector: 'hf-backtests-new',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, DecimalPipe, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests', link:'/backtests'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Backtest setup</div>
          <h1 style="margin-top:6px">Run a backtest</h1>
          <p style="color:var(--text-2);font-size:13px;margin-top:6px;max-width:560px">
            Pick a strategy or build one ad-hoc, set the window, configure folds, and we'll quote runtime and cost.
          </p>
        </div>
        <div class="head-actions">
          <a class="btn ghost" routerLink="/backtests">Cancel</a>
          <button class="btn">Save as preset</button>
          <button class="btn primary" (click)="submit()" [disabled]="submitting()">
            Run backtest <span class="kbd">⌘↵</span>
          </button>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:28px">
        <div style="display:flex;flex-direction:column;gap:18px">
          <section class="card">
            <div class="card-hd"><span class="title mono">01 · Identity</span></div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
              <div class="field"><label class="lbl">Name</label>
                <input class="input sans" name="name" [(ngModel)]="name" /></div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">02 · Universe</span></div>
            <div class="card-bd">
              <textarea class="input sans" rows="3" [(ngModel)]="universeStr" placeholder="AAPL, MSFT, GOOGL …"
                style="height:auto;padding:10px;line-height:18px;font-family:var(--font-mono);font-size:13px"></textarea>
              <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:6px">
                Default: {{ store.defaultUniverse().join(', ') || '(loading)' }}
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">03 · Date window</span></div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px">
              <div class="field"><label class="lbl">Master start</label>
                <input class="input" type="date" [(ngModel)]="startDate" /></div>
              <div class="field"><label class="lbl">Master end</label>
                <input class="input" type="date" [(ngModel)]="endDate" /></div>
              <div class="field"><label class="lbl">Starting cash</label>
                <input class="input" type="number" [(ngModel)]="startingCash" /></div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">04 · Walk-forward folds</span>
              <span class="pill"><span class="dot"></span>{{ foldCount() }} folds</span>
            </div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:14px">
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:14px">
                <div class="field"><label class="lbl">IS window (d)</label>
                  <input class="input" type="number" [(ngModel)]="isWindow" min="126" /></div>
                <div class="field"><label class="lbl">OOS (d)</label>
                  <input class="input" type="number" [(ngModel)]="oosWindow" min="21" /></div>
                <div class="field"><label class="lbl">Step (d)</label>
                  <input class="input" type="number" [(ngModel)]="stepDays" min="21" /></div>
                <div class="field"><label class="lbl">Candidates/fold</label>
                  <input class="input" type="number" [(ngModel)]="nCandidates" min="5" /></div>
              </div>

              <div style="display:grid;gap:2px;grid-template-columns:repeat(12,1fr);height:32px;border:1px solid var(--border);border-radius:6px;overflow:hidden">
                @for(i of folds(); track i){
                  <div [style.background]="i%2===0 ? 'var(--acc-long-soft)' : 'var(--acc-info-soft)'"
                       style="display:grid;place-items:center;font-family:var(--font-mono);font-size:10px;color:var(--text-2)">F{{ i+1 }}</div>
                }
              </div>
              <div class="mono" style="font-size:11px;color:var(--text-3)">
                ■ IS {{ isWindow }}d &nbsp; ■ OOS {{ oosWindow }}d · step {{ stepDays }}d &nbsp; · &nbsp; {{ startDate }} → {{ endDate }}
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">05 · Strategy parameters</span></div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
              <div class="field"><label class="lbl">IS objective</label>
                <select class="input sans" [(ngModel)]="objective">
                  <option value="sharpe">Sharpe</option><option value="sortino">Sortino</option><option value="calmar">Calmar</option>
                </select></div>
              <div class="field"><label class="lbl">Rebalance</label>
                <select class="input sans" [(ngModel)]="rebalance">
                  <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
                </select></div>
              <div class="field"><label class="lbl">Commission (bps)</label>
                <input class="input" type="number" [(ngModel)]="commissionBps" /></div>
              <div class="field"><label class="lbl">Spread (bps)</label>
                <input class="input" type="number" [(ngModel)]="spreadBps" /></div>
            </div>
          </section>

          <section class="card">
            <div class="card-hd"><span class="title mono">06 · Budget cap</span></div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:10px">
              <div class="field" style="max-width:200px"><label class="lbl">Max budget</label>
                <input class="input" type="number" step="0.01" [(ngModel)]="maxBudgetUsd" /><span class="suffix">USD</span></div>
              <div class="cost-gauge"><div class="fill" style="width:52%"></div><div class="mark" style="left:51.5%"></div></div>
              <div class="mono" style="font-size:11px;color:var(--text-3)">Monthly $ 412.10 / $ 800.00</div>
            </div>
          </section>
        </div>

        <aside style="position:sticky;top:64px;align-self:flex-start">
          <section class="card">
            <div class="card-hd"><span class="title">Live estimate</span>
              <span class="pill info live"><span class="dot"></span>{{ foldCount() }} folds</span>
            </div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
              <button class="btn" style="width:100%;justify-content:center" (click)="estimateCost()" [disabled]="estimating()">
                {{ estimating() ? 'Estimating…' : (estimate() ? 'Re-estimate' : 'Estimate cost') }}
              </button>
              @if(estimate(); as e){
                <div>
                  <div class="kpi" style="border:0;padding:0">
                    <div class="v" style="font-size:32px" [style.color]="e.exceeds_budget ? 'var(--acc-short-fg)' : 'var(--text)'">$ {{ e.est_total_usd | number:'1.2-2' }}</div>
                    <div class="mono" style="font-size:12px;color:var(--text-3)">cap $ {{ maxBudgetUsd | number:'1.2-2' }} · {{ e.by_agent?.length || 0 }} agents</div>
                  </div>
                </div>
                <div style="display:flex;flex-direction:column;gap:4px;max-height:180px;overflow:auto;border-top:1px solid var(--border);padding-top:8px">
                  @for(r of e.by_agent || []; track r.agent){
                    <div style="display:grid;grid-template-columns:1fr auto;gap:8px;font-size:11.5px;color:var(--text-2)">
                      <span>{{ r.agent }} <span class="mono" style="color:var(--text-3)">· {{ r.model }}</span></span>
                      <span class="mono">$ {{ r.total_usd | number:'1.2-4' }}</span>
                    </div>
                  }
                </div>
                @if(e.exceeds_budget){
                  <div style="background:var(--acc-short-soft);color:var(--acc-short-fg);font-size:12px;padding:8px;border-radius:6px">
                    ⚠ Estimated cost exceeds budget — raise the cap or shrink the run.
                  </div>
                }
              }
              @if(error()){<p style="color:var(--acc-short-fg);font-size:12px">{{ error() }}</p>}
              <button class="btn primary" style="height:36px;justify-content:center"
                [disabled]="submitting() || !estimate() || estimate()!.exceeds_budget"
                (click)="submit()">Run backtest <span class="kbd">⌘↵</span></button>
            </div>
          </section>
        </aside>
      </div>
    </hf-app-shell>
  `,
})
export class BacktestsNewPage implements OnInit {
  readonly store = inject(BacktestsStore);
  readonly modelsStore = inject(ModelsStore);
  private readonly router = inject(Router);

  overrides = signal<Record<string, string>>({});

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

  foldCount = computed(() => {
    const start = new Date(this.startDate).getTime();
    const end = new Date(this.endDate).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 0;
    const days = (end - start) / 86400000;
    return Math.max(1, Math.floor((days - this.isWindow) / Math.max(1, this.stepDays)));
  });
  folds = (): number[] => Array.from({ length: Math.min(12, this.foldCount()) }, (_, i) => i);

  ngOnInit(): void {
    this.store.loadDefaultUniverse().subscribe();
    this.modelsStore.loadAll().subscribe({ error: () => {} });
  }

  private parsedUniverse(): string[] {
    const raw = this.universeStr.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    return raw.length ? raw : this.store.defaultUniverse();
  }

  estimateCost(): void {
    this.estimating.set(true); this.error.set(null);
    this.store.estimate({
      universe: this.parsedUniverse(),
      start_date: this.startDate, end_date: this.endDate,
      rebalance_frequency: this.rebalance, max_budget_usd: this.maxBudgetUsd,
    }).subscribe({
      next: (est) => { this.estimating.set(false); this.estimate.set(est); },
      error: (e) => { this.estimating.set(false);
        const d = e?.error; this.error.set(typeof d === 'string' ? d : d?.detail || 'Failed to estimate');
      },
    });
  }

  submit(): void {
    const est = this.estimate();
    if (!est) { this.estimateCost(); return; }
    if (est.exceeds_budget) { this.error.set('Estimated cost exceeds budget.'); return; }
    this.submitting.set(true); this.error.set(null);
    this.store.create({
      name: this.name, universe: this.parsedUniverse(),
      start_date: this.startDate, end_date: this.endDate,
      starting_cash: this.startingCash, commission_bps: this.commissionBps, spread_bps: this.spreadBps,
      is_window_days: this.isWindow, oos_window_days: this.oosWindow, step_days: this.stepDays,
      n_candidates: this.nCandidates, is_objective: this.objective,
      rebalance_frequency: this.rebalance, baseline: this.baseline,
      max_budget_usd: this.maxBudgetUsd, model_overrides: this.overrides(),
    }).subscribe({
      next: (bt) => this.router.navigate(['/backtests', bt.id]),
      error: (e) => { this.submitting.set(false);
        const d = e?.error; this.error.set(typeof d === 'string' ? d : d?.detail || 'Failed to submit');
      },
    });
  }
}
