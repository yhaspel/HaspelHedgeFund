import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import { EstimateResponse } from '../../core/models/backtest.model';
import { ALL_PERSONAS } from '../../core/models/run.model';
import { ModelPanelComponent } from '../shared/model-panel.component';
import { PersonaCardComponent } from '../shared/persona-card.component';
import { GlossaryTermComponent } from '../shared/glossary-term.component';
import { apiErrorMessage } from '../../core/api/api-error';

// Mirrors apps/backtests/serializers.py (fix B3a §1). Kept next to the form so
// the two can be diffed at a glance when the backend contract moves.
const MIN_IS_WINDOW_DAYS = 126;
const MIN_OOS_WINDOW_DAYS = 21;
const MIN_BUDGET_USD = 0.05;
const MIN_STARTING_CASH = 0.01;
const MAX_UNIVERSE_SIZE = 200;
/** Tickers as the bar providers spell them: AAPL, BRK.B, RDS-A, plus digits. */
const BACKTEST_TICKER_RE = /^[A-Z0-9]{1,10}(?:[.\-][A-Z0-9]{1,4})?$/;

@Component({
  selector: 'hf-backtests-new',
  standalone: true,
  imports: [CommonModule, FormsModule, DecimalPipe, RouterLink, ModelPanelComponent, PersonaCardComponent, AppShellComponent, GlossaryTermComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests', link:'/backtests'}, {label:'New'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Backtest setup</div>
          <h1 class="mt-1.5">New walk-forward backtest</h1>
        </div>
      </div>

      @if (strategyId()) {
        <div class="card validation-context" role="note">
          <div class="card-bd flex items-start gap-2.5">
            <span aria-hidden="true">🔒</span>
            <p class="m-0 text-xs leading-[18px]">
              <strong>Autopilot validation run.</strong>
              This backtest is linked to your strategy — once it completes with a
              positive out-of-sample Sharpe and drawdown within the hard-halt limit,
              the strategy's <strong>Enable autopilot</strong> toggle unlocks.
              <a [routerLink]="['/fund/strategies', strategyId()]">Back to the fund strategy</a>
            </p>
          </div>
        </div>
      }

      <form #f="ngForm" (ngSubmit)="submit()" class="max-w-[760px] flex flex-col gap-[18px]"
            [attr.aria-describedby]="error() ? 'bt-form-error' : null">
        <section class="card">
          <div class="card-bd flex flex-col gap-3.5">
            <div class="field">
              <label class="lbl" for="bt-name">Name</label>
              <input id="bt-name" class="input sans" name="name" [(ngModel)]="name" required
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('name') ? 'true' : null"
                [attr.aria-describedby]="fieldError('name') ? 'bt-name-err' : null" />
              @if (fieldError('name'); as msg) {
                <p id="bt-name-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-name'">{{ msg }}</p>
              }
            </div>
            <div class="field">
              <label class="lbl" for="bt-universe"><hf-term key="universe">Universe</hf-term> <span class="text-text-3 normal-case tracking-normal font-normal">· comma-separated; blank uses default 20 quality names</span></label>
              <textarea id="bt-universe" class="input sans h-auto p-2.5 leading-[18px] font-mono text-xs" name="universe" [(ngModel)]="universeStr" rows="3"
                aria-describedby="bt-universe-default"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('universe') ? 'true' : null"
                placeholder="AAPL, MSFT, GOOGL, ..."></textarea>
              @if (fieldError('universe'); as msg) {
                <p id="bt-universe-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-universe'">{{ msg }}</p>
              }
              <div id="bt-universe-default" class="mono text-[11px] text-text-3 mt-1">
                Default universe: {{ store.defaultUniverse().join(', ') || '(loading)' }}
              </div>
            </div>
            <div class="grid grid-cols-2 gap-3.5">
              <div class="field"><label class="lbl" for="bt-start">Master start</label>
                <input id="bt-start" class="input" type="date" name="start" [(ngModel)]="startDate" required
                  (ngModelChange)="onFieldChange()"
                  [attr.aria-invalid]="fieldError('startDate') ? 'true' : null" />
              @if (fieldError('startDate'); as msg) {
                <p id="bt-start-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-startDate'">{{ msg }}</p>
              }</div>
              <div class="field"><label class="lbl" for="bt-end">Master end</label>
                <input id="bt-end" class="input" type="date" name="end" [(ngModel)]="endDate" required
                  (ngModelChange)="onFieldChange()"
                  [attr.aria-invalid]="fieldError('endDate') ? 'true' : null" />
              @if (fieldError('endDate'); as msg) {
                <p id="bt-end-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-endDate'">{{ msg }}</p>
              }</div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><h2 class="title">Walk-forward</h2></div>
          <div class="card-bd grid grid-cols-3 gap-3.5">
            <div class="field"><label class="lbl" for="bt-is-window">IS window (days)</label>
              <input id="bt-is-window" class="input" type="number" name="is_w" [(ngModel)]="isWindow" min="126"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('isWindow') ? 'true' : null" />
              @if (fieldError('isWindow'); as msg) {
                <p id="bt-is-window-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-isWindow'">{{ msg }}</p>
              }</div>
            <div class="field"><label class="lbl" for="bt-oos-window">OOS window (days)</label>
              <input id="bt-oos-window" class="input" type="number" name="oos_w" [(ngModel)]="oosWindow" min="21"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('oosWindow') ? 'true' : null" />
              @if (fieldError('oosWindow'); as msg) {
                <p id="bt-oos-window-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-oosWindow'">{{ msg }}</p>
              }</div>
            <div class="field"><label class="lbl" for="bt-step">Step (days)</label>
              <input id="bt-step" class="input" type="number" name="step_w" [(ngModel)]="stepDays" min="21"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('stepDays') ? 'true' : null" />
              @if (fieldError('stepDays'); as msg) {
                <p id="bt-step-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-stepDays'">{{ msg }}</p>
              }</div>
            <div class="field"><label class="lbl" for="bt-ncand">Candidates / fold</label>
              <input id="bt-ncand" class="input" type="number" name="ncand" [(ngModel)]="nCandidates" min="1" max="200"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('nCandidates') ? 'true' : null" />
              @if (fieldError('nCandidates'); as msg) {
                <p id="bt-ncand-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-nCandidates'">{{ msg }}</p>
              }</div>
            <div class="field"><label class="lbl" for="bt-objective">IS objective</label>
              <select id="bt-objective" class="input sans" name="obj" [(ngModel)]="objective"
                aria-describedby="bt-objective-note">
                <option value="sharpe">Sharpe</option>
                <option value="sortino">Sortino</option>
                <option value="calmar">Calmar</option>
              </select>
              <p id="bt-objective-note" class="text-[11px] text-text-3 m-0 mt-1">
                <hf-term key="sharpe">Sharpe</hf-term> / <hf-term key="sortino">Sortino</hf-term> · the score used to pick the best parameters in-sample.
              </p></div>
            <div class="field"><label class="lbl" for="bt-rebalance">Rebalance</label>
              <select id="bt-rebalance" class="input sans" name="rb" [(ngModel)]="rebalance"
                (ngModelChange)="onFieldChange()">
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select></div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd grid grid-cols-2 gap-3.5">
            <div class="field"><label class="lbl" for="bt-cash">Starting cash (USD)</label>
              <input id="bt-cash" class="input" type="number" name="cash" [(ngModel)]="startingCash" min="0.01"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('startingCash') ? 'true' : null" />
              @if (fieldError('startingCash'); as msg) {
                <p id="bt-cash-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-startingCash'">{{ msg }}</p>
              }</div>
            <div class="field"><label class="lbl" for="bt-baseline">Baseline</label>
              <select id="bt-baseline" class="input sans" name="bl" [(ngModel)]="baseline">
                <option value="universe_ew">Equal-weighted universe</option>
                <option value="spy">SPY</option>
              </select></div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd grid grid-cols-3 gap-3.5">
            <div class="field"><label class="lbl" for="bt-comm">Commission (bps)</label>
              <input id="bt-comm" class="input" type="number" name="comm" [(ngModel)]="commissionBps" min="0"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('commissionBps') ? 'true' : null" />
              @if (fieldError('commissionBps'); as msg) {
                <p id="bt-comm-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-commissionBps'">{{ msg }}</p>
              }</div>
            <div class="field"><label class="lbl" for="bt-spread">Spread (bps)</label>
              <input id="bt-spread" class="input" type="number" name="spr" [(ngModel)]="spreadBps" min="0"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('spreadBps') ? 'true' : null" />
              @if (fieldError('spreadBps'); as msg) {
                <p id="bt-spread-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-spreadBps'">{{ msg }}</p>
              }</div>
            <div class="field"><label class="lbl" for="bt-budget">Max budget (USD)</label>
              <input id="bt-budget" class="input" type="number" name="bud" [(ngModel)]="maxBudgetUsd" min="0.05" step="0.5"
                (ngModelChange)="onFieldChange()"
                [attr.aria-invalid]="fieldError('maxBudgetUsd') ? 'true' : null"
                aria-describedby="bt-budget-note" />
              @if (fieldError('maxBudgetUsd'); as msg) {
                <p id="bt-budget-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-maxBudgetUsd'">{{ msg }}</p>
              }
              <div id="bt-budget-note" class="mono text-[11px] text-text-3 mt-1">Run aborts if spend reaches this cap.</div></div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd">
            <div class="field">
              <label class="lbl" for="bt-graph">Agent graph</label>
              <select id="bt-graph" class="input sans" name="graph"
                [ngModel]="graphVersionId()" (ngModelChange)="onGraphChange($event)"
                data-testid="bt-graph-select">
                <option [ngValue]="null">Council classic (default)</option>
                @for (g of graphOptions(); track g.versionId) {
                  <option [ngValue]="g.versionId">{{ g.label }}</option>
                }
              </select>
              @if (graphVersionId()) {
                <p class="mono text-[11px] text-text-3 mt-1">
                  Models &amp; personas come from this saved graph.
                  <a routerLink="/graphs">Manage graphs</a>
                </p>
              }
            </div>
          </div>
        </section>

        @if (!graphVersionId()) {
        <section class="card">
          <div class="card-hd">
            <h2 class="title">Council shape</h2>
            <span class="pill"><span class="dot"></span>{{ selectedPersonas.size }} of {{ allPersonas.length }}</span>
          </div>
          <div class="card-bd flex flex-col gap-3.5">
            <div class="field">
              <span class="lbl">Personas <span class="text-text-3 normal-case tracking-normal font-normal">· each persona = one LLM call per ticker-day. Default to a single persona for backtests (the council is for live runs).</span></span>

              @if (fieldError('personas'); as msg) {
                <p id="bt-personas-err" class="text-[var(--acc-short-fg)] text-2xs mt-1 mb-0"
                   [attr.data-test]="'field-error-personas'">{{ msg }}</p>
              }
              <div class="persona-grid mt-1.5">
                @for (p of allPersonas; track p.id) {
                  <hf-persona-card
                    [persona]="p"
                    [selected]="selectedPersonas.has(p.id)"
                    (toggled)="togglePersona(p.id)" />
                }
              </div>
            </div>
            <div class="field">
              <label class="flex items-center gap-2 text-xs cursor-pointer">
                <input type="checkbox" name="include_cio" [(ngModel)]="includeCio"
                  (ngModelChange)="onFieldChange()"
                  data-test="include-cio" />
                <span><strong>Include CIO</strong> · runs the discretionary veto layer per ticker-day (matches live runs). Off by default — reproducibility-first.</span>
              </label>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-bd">
            <hf-model-panel [agents]="activeAgents()" [multiplier]="rebalanceCountEstimate()" [(overrides)]="overrides" />
          </div>
        </section>
        }

        @if (error()) {
          <p id="bt-form-error" role="alert" class="text-[var(--acc-short-fg)] text-2xs">{{ error() }}</p>
        }

        @if (graphVersionId()) {
          <button type="button" class="btn primary h-9 justify-center"
            (click)="submit()" [disabled]="submitting() || !isValid()" data-testid="bt-run-graph">
            {{ submitting() ? 'Submitting…' : 'Run walk-forward' }}
          </button>
        } @else if (!estimate()) {
          <button type="button" class="btn primary h-9 justify-center"
            (click)="estimateCost()" [disabled]="estimating() || !isValid()" data-test="bt-estimate">
            {{ estimating() ? 'Estimating…' : 'Estimate cost' }}
          </button>
        } @else {
          <section class="card"
            [style.borderColor]="estimate()!.exceeds_budget ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'"
            [style.background]="estimate()!.exceeds_budget ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'">
            <div class="card-bd flex flex-col gap-2.5">
              <div class="flex justify-between items-baseline">
                <h3 class="text-sm font-semibold m-0">Pre-flight estimate</h3>
                <button type="button" (click)="resetEstimate()" class="bt-link-button">
                  Edit &amp; re-estimate
                </button>
              </div>
              <div class="grid grid-cols-2 gap-y-2 gap-x-6 text-xs">
                <div>Estimated cost:
                  <span class="mono font-semibold"
                    [style.color]="estimate()!.exceeds_budget ? 'var(--acc-short-fg)' : 'var(--text)'">
                    $ {{ estimate()!.est_total_usd | number: '1.2-2' }}
                  </span>
                  <span class="text-text-3"> / cap $ {{ estimate()!.budget_cap_usd | number: '1.2-2' }}</span>
                </div>
                <div>LLM calls: <span class="mono">{{ estimate()!.n_llm_calls | number }}</span></div>
                <div>Rebalance days: <span class="mono">{{ estimate()!.n_rebalance_days }}</span></div>
                <div>Est. wall-time: <span class="mono">{{ estimate()!.est_minutes_optimistic | number: '1.0-1' }}–{{ estimate()!.est_minutes_upper | number: '1.0-1' }} min</span></div>
              </div>
              @if (estimate()!.exceeds_budget) {
                <p class="text-xs text-[var(--acc-short-fg)] font-medium m-0">
                  ⚠ Estimated cost exceeds your max budget. The run will abort partway.
                  Either raise the cap, shrink the universe/date range, or switch to a cheaper rebalance frequency.
                </p>
              }
              <details class="text-[11.5px] text-text-2">
                <summary class="cursor-pointer">Per-agent breakdown</summary>
                <table class="tbl mt-1.5">
                  <thead><tr>
                    <th>Agent</th><th>Model</th>
                    <th class="right">$/call</th><th class="right">Total</th>
                  </tr></thead>
                  <tbody>
                    @for (row of estimate()!.by_agent; track row.agent) {
                      <tr>
                        <td class="mono">{{ row.agent }}</td>
                        <td class="mono text-text-3">{{ row.model }}</td>
                        <td class="num">{{ row.per_call_usd | number: '1.4-5' }}</td>
                        <td class="num">{{ row.total_usd | number: '1.2-4' }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </details>
              <button type="submit" class="btn h-9 justify-center"
                [class.primary]="!estimate()!.exceeds_budget"
                [disabled]="submitting() || !isValid() || estimate()!.exceeds_budget">
                {{ submitting() ? 'Submitting…' : (estimate()!.exceeds_budget ? 'Over budget — raise cap to submit' : 'Confirm & start walk-forward') }}
              </button>
            </div>
          </section>
        }
      </form>
    </hf-app-shell>
  `,
  styles: [
    `
      .bt-link-button {
        font-size: 11.5px;
        color: var(--acc-info-fg);
        background: transparent;
        border: 0;
        cursor: pointer;
        padding: 0;
      }
      /* Mirrors the persona-grid layout in runs-new.page.ts so the two
         surfaces feel like the same UI primitive. */
      .persona-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
      }
      @media (max-width: 540px) {
        .persona-grid { grid-template-columns: 1fr; }
      }
    `,
  ],
})
export class BacktestsNewPage implements OnInit {
  readonly store = inject(BacktestsStore);
  readonly modelsStore = inject(ModelsStore);
  readonly graphs = inject(GraphsStore);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  // P7 §9: when arriving from a strategy's Autopilot page (?strategy=<id>), link
  // the backtest to that strategy so completing it unlocks the enable gate.
  readonly strategyId = signal<number | null>(null);

  graphVersionId = signal<number | null>(null);
  graphOptions = computed(() =>
    this.graphs
      .graphs()
      .filter((g) => g.latest_version && g.latest_version.validation_status === 'valid')
      .map((g) => ({
        versionId: g.latest_version!.id,
        label: g.name + (g.is_template ? ' (template)' : ''),
      })),
  );

  onGraphChange(v: number | null): void {
    this.graphVersionId.set(v ?? null);
    this._estimate.set(null);
  }

  // Shared `PersonaMeta[]` from runs.model — same source the Runs Console uses,
  // so the persona card UX stays consistent across surfaces (icon + tagline +
  // info tooltip + selected-state styling).
  readonly allPersonas = ALL_PERSONAS;
  readonly NON_PERSONA_AGENTS = [
    'fundamentals', 'technicals', 'valuation', 'sentiment',
    'macro', 'news_digest', 'risk_manager', 'portfolio_manager',
  ];
  // Default = single persona. The optimizer can still sweep PM thresholds /
  // sizing / vol-targeting against one persona; running all 8 is a 5-8×
  // cost multiplier with no proportional signal gain in a backtest.
  selectedPersonas = new Set<string>(['buffett']);
  includeCio = true;

  activeAgents = (): string[] => {
    const base = [...Array.from(this.selectedPersonas), ...this.NON_PERSONA_AGENTS];
    return this.includeCio ? [...base, 'cio'] : base;
  };

  togglePersona(p: string): void {
    if (this.selectedPersonas.has(p)) this.selectedPersonas.delete(p);
    else this.selectedPersonas.add(p);
    this.selectedPersonas = new Set(this.selectedPersonas);  // trigger CD
    // Personas are a direct cost multiplier — any change invalidates the
    // pre-flight estimate (see estimate()).
    this.onFieldChange();
  }

  overrides = signal<Record<string, string>>({});

  rebalanceCountEstimate(): number {
    const start = new Date(this.startDate).getTime();
    const end = new Date(this.endDate).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 1;
    const days = (end - start) / 86400000;
    const stride = this.rebalance === 'daily' ? 1 : this.rebalance === 'weekly' ? 5 : 21;
    const nUniv = (this.parsedUniverse().length || 20);
    // Multiplier should track agents actually firing, not the full council.
    const agentMultiplier = this.activeAgents().length;
    return Math.max(1, Math.round((days / stride) * nUniv * (agentMultiplier / 13)));
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
  private readonly _estimate = signal<EstimateResponse | null>(null);
  /** Fingerprint of the estimate-relevant inputs at the moment the estimate
   *  was taken. */
  private estimateKey: string | null = null;
  error = signal<string | null>(null);
  /** Field name → message, populated on every validity check. */
  readonly fieldErrors = signal<Record<string, string>>({});
  /** Show field errors only once the user has tried to estimate/submit. */
  readonly showErrors = signal(false);

  /**
   * The pre-flight estimate — or null once ANY input it was computed from has
   * changed. The estimate card used to stay on screen (and "Confirm & start"
   * stayed enabled) while the user grew the universe from 1 to 10 tickers and
   * switched monthly → daily, so a $1.50 quote could green-light a run costing
   * two orders of magnitude more.
   */
  estimate(): EstimateResponse | null {
    const est = this._estimate();
    if (!est) return null;
    return this.estimateKey === this.currentEstimateKey() ? est : null;
  }

  /** Every input the /backtests/estimate/ payload (or the cost) depends on. */
  private currentEstimateKey(): string {
    return JSON.stringify([
      this.parsedUniverse(),
      this.startDate,
      this.endDate,
      this.rebalance,
      this.maxBudgetUsd,
      this.overrides(),
      [...this.selectedPersonas].sort(),
      this.includeCio,
      this.graphVersionId(),
    ]);
  }

  ngOnInit(): void {
    const sid = Number(this.route.snapshot.queryParamMap.get('strategy'));
    if (Number.isFinite(sid) && sid > 0) {
      this.strategyId.set(sid);
      this.name = 'Validation WF ' + new Date().toISOString().slice(0, 10);
      // phase-09a — pre-fill a cheap-but-complete validation config from the
      // strategy: full universe, one persona, CIO on, monthly rebalance, the
      // book's cash. On error, keep the static defaults so the page stays usable.
      this.store.strategyDefaults(sid).subscribe({
        next: (d) => {
          if (d.universe?.length) this.universeStr = d.universe.join(', ');
          this.selectedPersonas = new Set(d.personas?.length ? d.personas : ['buffett']);
          this.includeCio = d.include_cio;
          this.rebalance = d.rebalance_frequency;     // monthly — the cost lever
          if (d.starting_cash != null) this.startingCash = d.starting_cash;  // 0 is valid (fully-invested book)
          if (d.name) this.name = d.name;
        },
        error: () => { /* keep static defaults — page stays usable */ },
      });
    }
    this.store.loadDefaultUniverse().subscribe();
    this.modelsStore.loadAll().subscribe();
    this.graphs.loadGraphs().subscribe();
  }

  private parsedUniverse(): string[] {
    const raw = this.universeStr
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    // Dedupe — the serializer rejects duplicates, and "aapl, AAPL" is a typo,
    // not a request for two positions.
    const deduped = [...new Set(raw)];
    return deduped.length ? deduped : this.store.defaultUniverse();
  }

  /**
   * Client-side mirror of apps/backtests/serializers.py's input contract
   * (fix B3a §1). The `min="…"` attributes on the number inputs were advisory
   * only — Angular sets novalidate on the form — so step_days=0 (an infinite
   * loop in generate_folds()), starting_cash=0 (ZeroDivisionError mid-run) and
   * negative bps all reached a Celery worker. Now they never leave the page.
   */
  private validate(): Record<string, string> {
    const errs: Record<string, string> = {};
    if (!this.name.trim()) errs['name'] = 'Give the backtest a name.';

    const start = Date.parse(this.startDate);
    const end = Date.parse(this.endDate);
    if (!Number.isFinite(start)) errs['startDate'] = 'Pick a master start date.';
    if (!Number.isFinite(end)) errs['endDate'] = 'Pick a master end date.';
    if (Number.isFinite(start) && Number.isFinite(end) && end <= start) {
      errs['endDate'] = 'The master end date must be after the start date.';
    }

    if (!(this.isWindow >= MIN_IS_WINDOW_DAYS)) {
      errs['isWindow'] = `In-sample window must be at least ${MIN_IS_WINDOW_DAYS} days.`;
    }
    if (!(this.oosWindow >= MIN_OOS_WINDOW_DAYS)) {
      errs['oosWindow'] = `Out-of-sample window must be at least ${MIN_OOS_WINDOW_DAYS} days.`;
    }
    if (!(this.stepDays >= 1)) {
      errs['stepDays'] = 'Step must be at least 1 day.';
    } else if (this.oosWindow >= MIN_OOS_WINDOW_DAYS && this.stepDays < this.oosWindow) {
      // Overlapping OOS folds double-count sessions; the stitched curve cannot
      // represent them.
      errs['stepDays'] = `Step must be at least the OOS window (${this.oosWindow} days) — a smaller step overlaps folds.`;
    }
    if (
      Number.isFinite(start) && Number.isFinite(end) && end > start
      && this.isWindow >= MIN_IS_WINDOW_DAYS && this.oosWindow >= MIN_OOS_WINDOW_DAYS
      && (end - start) / 86_400_000 < this.isWindow + this.oosWindow
    ) {
      errs['endDate'] =
        `The master window needs at least ${this.isWindow + this.oosWindow} days for one fold.`;
    }
    if (!(this.nCandidates >= 1)) errs['nCandidates'] = 'At least 1 candidate per fold.';
    if (!(this.startingCash >= MIN_STARTING_CASH)) {
      errs['startingCash'] = 'Starting cash must be greater than zero.';
    }
    if (!(this.commissionBps >= 0)) errs['commissionBps'] = 'Commission cannot be negative.';
    if (!(this.spreadBps >= 0)) errs['spreadBps'] = 'Spread cannot be negative.';
    if (!(this.maxBudgetUsd >= MIN_BUDGET_USD)) {
      errs['maxBudgetUsd'] = `Max budget must be at least $${MIN_BUDGET_USD.toFixed(2)} — it is the spend kill-switch.`;
    }

    const universe = this.parsedUniverse();
    if (universe.length === 0) errs['universe'] = 'Add at least one ticker.';
    else if (universe.length > MAX_UNIVERSE_SIZE) {
      errs['universe'] = `At most ${MAX_UNIVERSE_SIZE} tickers.`;
    } else {
      const bad = universe.filter((t) => !BACKTEST_TICKER_RE.test(t));
      if (bad.length) {
        errs['universe'] = `Not valid symbols: ${bad.slice(0, 5).join(', ')}${bad.length > 5 ? '…' : ''}`;
      }
    }
    if (!this.graphVersionId() && this.selectedPersonas.size === 0) {
      errs['personas'] = 'Pick at least one persona (or choose a saved agent graph).';
    }
    return errs;
  }

  /** Re-run validation, publish the messages, and report whether we may go on. */
  private checkValid(): boolean {
    const errs = this.validate();
    this.fieldErrors.set(errs);
    this.showErrors.set(true);
    const keys = Object.keys(errs);
    if (keys.length) {
      this.error.set('Fix the highlighted fields before continuing.');
      return false;
    }
    this.error.set(null);
    return true;
  }

  /** Live validity for the disabled-state bindings (cheap; no side effects). */
  isValid(): boolean {
    return Object.keys(this.validate()).length === 0;
  }

  /** Re-check as the user types, once they have seen the errors at least once. */
  onFieldChange(): void {
    if (this.showErrors()) this.fieldErrors.set(this.validate());
  }

  fieldError(key: string): string | null {
    return this.showErrors() ? (this.fieldErrors()[key] ?? null) : null;
  }

  resetEstimate(): void {
    this._estimate.set(null);
    this.estimateKey = null;
    this.error.set(null);
  }

  estimateCost(): void {
    if (!this.checkValid()) return;
    this.estimating.set(true);
    this.error.set(null);
    const key = this.currentEstimateKey();
    // P02c review: estimate must use the same model_overrides and personas
    // that the eventual create request will use. Otherwise the displayed
    // cost can diverge from the actual run cost after the user changes
    // model selections.
    this.store.estimate({
      universe: this.parsedUniverse(),
      start_date: this.startDate,
      end_date: this.endDate,
      rebalance_frequency: this.rebalance,
      max_budget_usd: this.maxBudgetUsd,
      model_overrides: this.overrides(),
      personas: Array.from(this.selectedPersonas),
    }).subscribe({
      next: (est) => {
        this.estimating.set(false);
        this.estimateKey = key;
        this._estimate.set(est);
      },
      error: (e: unknown) => {
        this.estimating.set(false);
        // Was `JSON.stringify(detail)` — a raw `{"step_days":["..."]}` dump.
        this.error.set(apiErrorMessage(e, 'Failed to estimate'));
      },
    });
  }

  submit(): void {
    if (!this.checkValid()) return;
    const gv = this.graphVersionId();
    const est = this.estimate();
    // In ad-hoc mode a CURRENT estimate is required first (estimate() returns
    // null once any estimate-relevant input changed); with a graph selected the
    // server-side max_budget_usd cap still protects, so submit directly.
    if (!gv && !est) {
      this.error.set('Re-run the cost estimate — the run settings changed since the last one.');
      return;
    }
    if (est?.exceeds_budget) {
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
      model_overrides: gv ? {} : this.overrides(),
      personas: gv ? [] : Array.from(this.selectedPersonas),
      disable_cio: !this.includeCio,
      graph_version_id: gv,
      strategy_id: this.strategyId(),
    }).subscribe({
      next: (bt) => this.router.navigate(['/backtests', bt.id]),
      error: (e: unknown) => {
        this.submitting.set(false);
        // The backend now 400s on step_days=0 / starting_cash=0 / negative bps
        // and on an overlapping-fold step; show its message, not a JSON dump.
        this.error.set(apiErrorMessage(e, 'Failed to submit'));
      },
    });
  }
}
