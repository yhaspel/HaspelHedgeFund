import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  CategoryScale,
  Chart,
  ChartConfiguration,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js';

import { ApiClient } from '../../core/api/api-client';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { AppShellComponent } from '../shared/app-shell.component';
import { TickerComponent } from '../shared/ticker.component';
import { ENTRY_ANIMATION, baseLegend, readChartTheme } from '../shared/chart-defaults';

Chart.register(
  LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend,
);

interface AgentRow {
  agent_name: string;
  model_id: string;
  n_decisions: number;
  n_directional: number;
  hit_rate: string | null;
  hit_rate_ci_low: string | null;
  hit_rate_ci_high: string | null;
  brier_score: string | null;
  avg_forward_return_bps: string | null;
  pnl_contribution_bps: string | null;
  n_contrarian_decisions: number;
  contrarian_hit_rate: string | null;
  contrarian_hit_rate_ci_low: string | null;
  contrarian_hit_rate_ci_high: string | null;
  provisional: boolean;
  /** WAVE 3 — generation of the scoring maths behind this row. */
  metrics_version?: number;
}
interface ModelRow {
  model_id: string;
  agent_role: string;
  n_decisions: number;
  avg_cost_per_decision_usd: string | null;
  cost_adjusted_return_bps: string | null;
  provisional: boolean;
}
interface StrategyRow {
  strategy: number | null;
  strategy_name: string | null;
  flavor: string;
  flavor_display: string;
  n_cycles: number;
  /**
   * WAVE 3 — the DISJOINT holding intervals actually priced. This, not
   * `n_cycles`, is the sample size behind every ratio in the row: a cycle that
   * could not be marked contributes nothing. Provisional is `n_observations <
   * 20`, so showing cycles alone made the "prov." pill look arbitrary.
   */
  n_observations: number;
  /** Annualisation factor from the OBSERVED cadence, not a hard-coded 252. */
  periods_per_year: string | null;
  /** Why `sortino` is null or capped ("" when it is a plain number). */
  sortino_note: string;
  /** Generation of the scoring maths. Below METRICS_VERSION ⇒ ratios NULLed. */
  metrics_version: number;
  annualised_return_pct: string | null;
  total_return_pct: string | null;
  sharpe: string | null;
  sortino: string | null;
  max_drawdown_pct: string | null;
  hit_rate: string | null;
  annualised_turnover_pct: string | null;
  avg_cost_per_cycle_usd: string | null;
  sharpe_p25: string | null;
  sharpe_p75: string | null;
  sortino_p25: string | null;
  sortino_p75: string | null;
  max_drawdown_p25_pct: string | null;
  max_drawdown_p75_pct: string | null;
  council_alpha_bps: string | null;
  council_cost_usd: string | null;
  council_net_value_usd: string | null;
  baseline_version: string;
  provisional: boolean;
}
interface CouncilAlphaRow {
  as_of_date: string;
  realised_pct: number;
  baseline_pct: number;
  cum_realised_pct: number;
  cum_baseline_pct: number;
}
interface DecisionRow {
  run_id: number;
  ticker: string;
  as_of_date: string;
  signal: string | null;
  confidence: number | null;
  forward_return_pct: number | null;
}

@Component({
  selector: 'hf-leaderboard-page',
  standalone: true,
  imports: [CommonModule, AppShellComponent, TickerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Leaderboard' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Performance</div>
          <h1 class="mt-1.5">Leaderboard</h1>
          <p class="sub">
            Which agents and strategies have actually produced good calls over
            the rolling forward-test window.
          </p>
        </div>
        <div class="head-right">
          <select
            class="sel"
            [value]="window()"
            (change)="onWindow($any($event.target).value)"
            aria-label="Window"
          >
            <option value="30d">30 days</option>
            <option value="90d">90 days</option>
            <option value="lifetime">Lifetime</option>
          </select>
          <button class="btn sm" (click)="recompute()" [disabled]="busy()">
            {{ busy() ? 'Recomputing…' : 'Recompute' }}
          </button>
        </div>
      </div>

      <div class="tabs mb-3.5" role="tablist">
        <button
          class="tab"
          [class.active]="tab() === 'agents'"
          role="tab"
          [attr.aria-selected]="tab() === 'agents'"
          (click)="tab.set('agents')"
        >
          Agents
        </button>
        <button
          class="tab"
          [class.active]="tab() === 'strategies'"
          role="tab"
          [attr.aria-selected]="tab() === 'strategies'"
          (click)="tab.set('strategies')"
        >
          Strategies
        </button>
      </div>

      @if (tab() === 'agents') {
        <section class="card">
          <div class="card-hd"><h2 class="title">Top personas</h2></div>
          @if (agents().length === 0) {
            <p class="empty">
              No scored decisions yet for this window. The leaderboard fills in
              once runs have a forward-return window (≈5 trading days) behind
              them. Try “Recompute”.
            </p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Persona</th>
                    <th>Model</th>
                    <th class="r">n</th>
                    <th class="r">Hit rate</th>
                    <th class="r">Brier</th>
                    <th class="r">Avg fwd (bps)</th>
                    <th class="r">PnL (bps)</th>
                  </tr>
                </thead>
                <tbody>
                  @for (a of agents(); track a.agent_name + a.model_id) {
                    <tr class="clk" (click)="drill(a.agent_name)">
                      <td>
                        {{ a.agent_name }}
                        @if (a.provisional) {
                          <span class="pill ml-1.5 align-middle" title="Fewer than 20 measurable observations — every ratio is withheld">prov.</span>
                        }
                      </td>
                      <td class="muted">{{ a.model_id || '—' }}</td>
                      <td class="r">{{ a.n_directional }}</td>
                      <td class="r">{{ pct(a.hit_rate) }}</td>
                      <td class="r">{{ num(a.brier_score) }}</td>
                      <td class="r">{{ num(a.avg_forward_return_bps) }}</td>
                      <td class="r">{{ a.pnl_contribution_bps ?? '—' }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>

        <section class="card">
          <div class="card-hd"><h2 class="title">Top models per role</h2></div>
          @if (models().length === 0) {
            <p class="empty">No model data for this window.</p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Role</th>
                    <th class="r">n</th>
                    <th class="r">$/decision</th>
                    <th class="r">Cost-adj (bps/$)</th>
                  </tr>
                </thead>
                <tbody>
                  @for (m of models(); track m.model_id + m.agent_role) {
                    <tr>
                      <td>{{ m.model_id }}</td>
                      <td class="muted">{{ m.agent_role }}</td>
                      <td class="r">{{ m.n_decisions }}</td>
                      <td class="r">{{ usd(m.avg_cost_per_decision_usd) }}</td>
                      <td class="r">{{ num(m.cost_adjusted_return_bps) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>

        <section class="card">
          <div class="card-hd"><h2 class="title">Useful contrarians</h2></div>
          <p class="sub tight">
            Personas ranked by how often they were right <em>when they went
            against the run's majority signal</em> — the contrarians worth
            weighting up. Hit rate over disagreement cases only.
          </p>
          @if (contrarians().length === 0) {
            <p class="empty">
              No contrarian decisions scored yet for this window — needs runs
              where a persona dissented from the consensus and a forward-return
              window has elapsed.
            </p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Persona</th>
                    <th>Model</th>
                    <th class="r">Disagreements</th>
                    <th class="r">Contrarian hit rate</th>
                  </tr>
                </thead>
                <tbody>
                  @for (a of contrarians(); track a.agent_name + a.model_id) {
                    <tr class="clk" (click)="drill(a.agent_name)">
                      <td>
                        {{ a.agent_name }}
                        @if (a.n_contrarian_decisions < 10) {
                          <span class="pill ml-1.5 align-middle" title="Fewer than 10 disagreements — provisional">prov.</span>
                        }
                      </td>
                      <td class="muted">{{ a.model_id || '—' }}</td>
                      <td class="r">{{ a.n_contrarian_decisions }}</td>
                      <td class="r">
                        <span
                          [class.pos]="num0(a.contrarian_hit_rate) >= 0.5"
                          [class.neg]="num0(a.contrarian_hit_rate) < 0.5"
                          [title]="contrarianTip(a)"
                          ><span aria-hidden="true">{{ num0(a.contrarian_hit_rate) >= 0.5 ? '✓' : '✗' }}</span>
                          {{ pct(a.contrarian_hit_rate) }}</span
                        >
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>

        @if (drillAgent()) {
          <section class="card">
            <div class="card-hd">
              <h2 class="title">Decisions — {{ drillAgent() }}</h2>
              <button class="btn sm ghost" (click)="drillAgent.set(null)">Close</button>
            </div>
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Ticker</th>
                    <th>Name</th>
                    <th>As of</th>
                    <th>Signal</th>
                    <th class="r">Conf</th>
                    <th class="r">Fwd 5d</th>
                  </tr>
                </thead>
                <tbody>
                  @for (d of decisions(); track d.run_id) {
                    <tr class="clk" (click)="openRun(d.run_id)">
                      <td>#{{ d.run_id }}</td>
                      <td><hf-ticker [ticker]="d.ticker" [disablePopover]="true"></hf-ticker></td>
                      <td class="muted name-cell">{{ nameFor(d.ticker) }}</td>
                      <td class="muted">{{ d.as_of_date }}</td>
                      <td>{{ d.signal }}</td>
                      <td class="r">{{ d.confidence ?? '—' }}</td>
                      <td class="r">
                        {{ d.forward_return_pct !== null ? d.forward_return_pct + '%' : '—' }}
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            <div class="drill-foot">
              <span class="micro" data-test="decisions-count">
                Showing {{ decisions().length }} decision(s){{
                  decisionsHasMore() ? ' — more available' : ''
                }}.
              </span>
              @if (decisionsHasMore()) {
                <button
                  class="btn sm"
                  data-test="decisions-load-more"
                  [disabled]="decisionsLoading()"
                  (click)="moreDecisions()"
                >
                  {{ decisionsLoading() ? 'Loading…' : 'Load more' }}
                </button>
              }
            </div>
          </section>
        }
      } @else {
        <section class="card">
          <div class="card-hd">
            <h2 class="title">My strategies</h2>
            <select
              class="sel sm"
              [value]="stratSort()"
              (change)="onStratSort($any($event.target).value)"
              aria-label="Sort strategies"
            >
              <option value="sharpe">Sort: Sharpe</option>
              <option value="council_alpha_bps">Sort: Council α</option>
              <option value="total_return_pct">Sort: Return</option>
            </select>
          </div>
          @if (strategies().length === 0) {
            <p class="empty">
              No strategy cycles scored yet for this window. Rows appear once a
              strategy has ≥ 2 completed cycles with marks.
            </p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Flavor</th>
                    <th class="r">Cycles</th>
                    <th class="r" title="Disjoint priced holding intervals — the sample behind every ratio">Obs</th>
                    <th class="r">Ann. return</th>
                    <th class="r">Sharpe</th>
                    <th class="r">Sortino</th>
                    <th class="r">Max DD</th>
                    <th class="r">Turnover</th>
                    <th class="r">$/cycle</th>
                    <th class="r">Council α</th>
                  </tr>
                </thead>
                <tbody>
                  @for (s of strategies(); track s.strategy) {
                    <tr class="clk" (click)="openStrategyDrill(s)">
                      <td>
                        {{ s.strategy_name }}
                        @if (s.provisional) {
                          <span class="pill ml-1.5 align-middle"
                                [title]="provTip(s)"
                                data-test="leaderboard-prov">prov.</span>
                        }
                      </td>
                      <td class="muted">{{ s.flavor_display }}</td>
                      <td class="r">{{ s.n_cycles }}</td>
                      <td class="r" [attr.data-test]="'leaderboard-obs-' + s.flavor">
                        {{ s.n_observations ?? '—' }}
                        @if (s.periods_per_year) {
                          <div class="micro">{{ num(s.periods_per_year) }}/yr</div>
                        }
                      </td>
                      <td class="r"><span [title]="ratioTip(s)">{{ pctRaw(s.annualised_return_pct) }}</span></td>
                      <td class="r"><span [title]="ratioTip(s)" data-test="leaderboard-sharpe">{{ num(s.sharpe) }}</span></td>
                      <td class="r">
                        <span [title]="sortinoTip(s)" data-test="leaderboard-sortino">{{ num(s.sortino) }}</span>
                        @if (s.sortino === null && s.sortino_note) {
                          <div class="micro" data-test="leaderboard-sortino-note">{{ s.sortino_note }}</div>
                        }
                      </td>
                      <td class="r">{{ pctRaw(s.max_drawdown_pct) }}</td>
                      <td class="r">{{ pctRaw(s.annualised_turnover_pct) }}</td>
                      <td class="r">{{ usd(s.avg_cost_per_cycle_usd) }}</td>
                      <td class="r">
                        @if (s.council_alpha_bps !== null) {
                          <span
                            [class.pos]="num0(s.council_alpha_bps) >= 0"
                            [class.neg]="num0(s.council_alpha_bps) < 0"
                            [title]="councilTip(s)"
                            >{{ bps(s.council_alpha_bps) }}</span
                          >
                          <div class="micro">
                            {{ usd0(s.council_net_value_usd) }} · cost
                            {{ usd0(s.council_cost_usd) }}
                          </div>
                        } @else {
                          <span
                            class="muted"
                            title="Needs 30 days of baseline cycles before council-alpha is meaningful"
                            >—</span
                          >
                        }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>

        @if (drillStrategy()) {
          <section class="card">
            <div class="card-hd">
              <h2 class="title">Council α over time — {{ drillStrategy()!.strategy_name }}</h2>
              <button class="btn sm ghost" (click)="closeStrategyDrill()">Close</button>
            </div>
            @if (councilSeries().length === 0) {
              <p class="empty">
                No paired baseline cycles in this window yet. Council-alpha is
                captured forward-only, so the chart fills in as new cycles run.
              </p>
            } @else {
              <p class="sub tight">
                Cumulative return of the live (council) book vs the council-free
                deterministic baseline. The gap is what the council added.
              </p>
              <div class="chart-wrap"><canvas #councilAlphaChart></canvas></div>
            }
          </section>
        }

        <section class="card">
          <div class="card-hd"><h2 class="title">Flavor benchmarks</h2></div>
          <p class="sub tight">
            Median across your own strategies of each flavor (single-tenant).
          </p>
          @if (flavors().length === 0) {
            <p class="empty">No flavor aggregates yet.</p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Flavor</th>
                    <th class="r">Strategies</th>
                    <th class="r" title="Disjoint priced holding intervals across the flavor">Obs</th>
                    <th class="r">Median Sharpe</th>
                    <th class="r">Median Sortino</th>
                    <th class="r">Median Max DD</th>
                  </tr>
                </thead>
                <tbody>
                  @for (f of flavors(); track f.flavor) {
                    <tr>
                      <td>
                        {{ f.flavor_display }}
                        @if (f.provisional) {
                          <span class="pill ml-1.5 align-middle"
                                [title]="provTip(f)"
                                data-test="leaderboard-flavor-prov">prov.</span>
                        }
                      </td>
                      <td class="r">{{ f.n_cycles }}</td>
                      <td class="r">{{ f.n_observations ?? '—' }}</td>
                      <td class="r">
                        <span [title]="ratioTip(f)">{{ num(f.sharpe) }}</span>
                        <div class="micro">{{ iqr(f.sharpe_p25, f.sharpe_p75) }}</div>
                      </td>
                      <td class="r">
                        <span [title]="sortinoTip(f)">{{ num(f.sortino) }}</span>
                        <div class="micro">
                          {{ f.sortino === null && f.sortino_note ? f.sortino_note : iqr(f.sortino_p25, f.sortino_p75) }}
                        </div>
                      </td>
                      <td class="r">
                        {{ pctRaw(f.max_drawdown_pct) }}
                        <div class="micro">
                          {{ iqr(f.max_drawdown_p25_pct, f.max_drawdown_p75_pct, '%') }}
                        </div>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:16px; }
      .sub { color: var(--text-3); font-size: 12.5px; margin-top: 4px; max-width: 640px; }
      .sub.tight { margin: -4px 0 10px; }
      .drill-foot { display:flex; align-items:center; gap:10px; padding:8px 12px; }
      .drill-foot .micro { font-size:10.5px; color: var(--text-3); }
      .head-right { display:flex; gap:8px; align-items:center; }
      .sel { padding:6px 10px; border-radius: var(--r-6); background: var(--surface-2); color: var(--text); border:1px solid var(--border); }
      .sel.sm { padding:4px 8px; font-size:12px; }
      .card-hd { display:flex; align-items:center; justify-content:space-between; gap:12px; }
      .tbl .pos { color: var(--acc-long-fg); }
      .tbl .neg { color: var(--acc-short-fg); }
      .tbl .micro { font-size:10.5px; color: var(--text-3); margin-top:2px; white-space:nowrap; }
      .card { margin-bottom: 16px; }
      .empty { color: var(--text-3); font-size: 13px; padding: 8px 2px; }
      .tbl .r { text-align: right; }
      .tbl .muted { color: var(--text-3); }
      .tbl .clk { cursor: pointer; }
      .tbl .clk:hover { background: var(--surface-2); }
      .tbl .name-cell { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .chart-wrap { height: 260px; position: relative; }
    `,
  ],
})
export class LeaderboardPage implements OnInit, AfterViewInit, OnDestroy {
  private readonly api = inject(ApiClient);
  protected readonly tickerProfiles = inject(TickerProfileStore);

  @ViewChild('councilAlphaChart', { static: false })
  councilCanvas?: ElementRef<HTMLCanvasElement>;
  private councilChart: Chart | null = null;
  private viewReady = false;

  constructor() {
    // Re-render the council-alpha chart whenever its series changes (after the
    // canvas exists). setTimeout defers to the next tick so the @if has rendered.
    effect(() => {
      this.councilSeries();
      if (this.viewReady) setTimeout(() => this.renderCouncilAlpha(), 0);
    });
  }

  readonly tab = signal<'agents' | 'strategies'>('agents');
  readonly window = signal<'30d' | '90d' | 'lifetime'>('90d');
  readonly stratSort = signal<'sharpe' | 'council_alpha_bps' | 'total_return_pct'>('sharpe');
  readonly busy = signal(false);

  readonly agents = signal<AgentRow[]>([]);
  readonly models = signal<ModelRow[]>([]);
  readonly strategies = signal<StrategyRow[]>([]);
  // "Useful contrarians": personas that have any contrarian decisions, ranked by
  // how often they were right when dissenting from the run consensus.
  readonly contrarians = computed(() =>
    this.agents()
      .filter((a) => a.n_contrarian_decisions > 0)
      .sort(
        (a, b) =>
          Number(b.contrarian_hit_rate ?? -1) - Number(a.contrarian_hit_rate ?? -1) ||
          b.n_contrarian_decisions - a.n_contrarian_decisions,
      ),
  );
  readonly flavors = signal<StrategyRow[]>([]);
  readonly drillAgent = signal<string | null>(null);
  readonly decisions = signal<DecisionRow[]>([]);
  // WAVE 3 — the drill-down is paged server-side (`limit`/`offset`, limit ≤ 200)
  // and single-tenant. The endpoint returns no total, so "there is a next page"
  // is inferred from a full page coming back — the same rule the server uses.
  readonly decisionsOffset = signal(0);
  readonly decisionsLoading = signal(false);
  readonly decisionsHasMore = signal(false);
  readonly DECISIONS_PAGE = 50;
  readonly drillStrategy = signal<StrategyRow | null>(null);
  readonly councilSeries = signal<CouncilAlphaRow[]>([]);

  ngAfterViewInit(): void {
    this.viewReady = true;
  }

  ngOnDestroy(): void {
    this.councilChart?.destroy();
  }

  ngOnInit(): void {
    this.load();
  }

  onWindow(w: string): void {
    this.window.set(w as '30d' | '90d' | 'lifetime');
    this.load();
    const s = this.drillStrategy();
    if (s) this.fetchCouncilSeries(s); // keep an open chart in sync with the window
  }

  onStratSort(sort: string): void {
    this.stratSort.set(sort as 'sharpe' | 'council_alpha_bps' | 'total_return_pct');
    this.loadStrategies();
  }

  load(): void {
    const w = this.window();
    this.api
      .get<{ rows: AgentRow[] }>(`/leaderboard/agents/?window=${w}`)
      .subscribe((r) => this.agents.set(r.rows ?? []));
    this.api
      .get<{ rows: ModelRow[] }>(`/leaderboard/models/?window=${w}`)
      .subscribe((r) => this.models.set(r.rows ?? []));
    this.loadStrategies();
    this.api
      .get<{ rows: StrategyRow[] }>(`/leaderboard/strategies/by-flavor/?window=${w}`)
      .subscribe((r) => this.flavors.set(r.rows ?? []));
  }

  /**
   * WAVE 3: the server orders provisional rows LAST whatever column was asked
   * for (a Sharpe-less 3-cycle row must never head the table just because the
   * sort column is null-friendly). Rows are stored and rendered in the exact
   * order they arrive — do NOT re-sort client-side, that would undo it.
   */
  loadStrategies(): void {
    this.api
      .get<{ rows: StrategyRow[] }>(
        `/leaderboard/strategies/?window=${this.window()}&sort=${this.stratSort()}`,
      )
      .subscribe((r) => this.strategies.set(r.rows ?? []));
  }

  drill(agent: string): void {
    this.drillAgent.set(agent);
    this.decisions.set([]);
    this.decisionsOffset.set(0);
    this.decisionsHasMore.set(false);
    this.fetchDecisions(agent, 0, false);
  }

  /** Next page of the agent drill-down (cursor is a plain offset). */
  moreDecisions(): void {
    const agent = this.drillAgent();
    if (!agent || this.decisionsLoading() || !this.decisionsHasMore()) return;
    this.fetchDecisions(agent, this.decisionsOffset() + this.DECISIONS_PAGE, true);
  }

  private fetchDecisions(agent: string, offset: number, append: boolean): void {
    this.decisionsLoading.set(true);
    this.api
      .get<{ decisions: DecisionRow[]; limit?: number; offset?: number }>(
        `/leaderboard/agents/${agent}/decisions/?window=${this.window()}` +
          `&limit=${this.DECISIONS_PAGE}&offset=${offset}`,
      )
      .subscribe({
        next: (r) => {
          const rows = r.decisions ?? [];
          this.decisions.set(append ? [...this.decisions(), ...rows] : rows);
          this.decisionsOffset.set(offset);
          // A short page is the last page; a full one MAY have more.
          this.decisionsHasMore.set(rows.length === this.DECISIONS_PAGE);
          this.decisionsLoading.set(false);
          const tickers = [...new Set(rows.map((d) => d.ticker))];
          if (tickers.length) this.tickerProfiles.fetchNames(tickers).subscribe();
        },
        error: () => {
          this.decisionsLoading.set(false);
          this.decisionsHasMore.set(false);
        },
      });
  }

  nameFor(ticker: string): string {
    // _bump is read for reactivity within Angular's CD pass.
    void this.tickerProfiles._bump();
    return this.tickerProfiles.name(ticker) || '—';
  }

  openStrategyDrill(s: StrategyRow): void {
    if (s.strategy === null) return; // flavor-aggregate rows aren't drillable
    this.drillStrategy.set(s);
    this.fetchCouncilSeries(s);
  }

  closeStrategyDrill(): void {
    this.drillStrategy.set(null);
    this.councilSeries.set([]);
    this.councilChart?.destroy();
    this.councilChart = null;
  }

  private fetchCouncilSeries(s: StrategyRow): void {
    this.api
      .get<{ rows: CouncilAlphaRow[] }>(
        `/leaderboard/strategies/${s.strategy}/council-alpha/?window=${this.window()}`,
      )
      .subscribe((r) => this.councilSeries.set(r.rows ?? []));
  }

  private renderCouncilAlpha(): void {
    const rows = this.councilSeries();
    const canvas = this.councilCanvas?.nativeElement;
    if (!canvas || rows.length === 0) return;
    this.councilChart?.destroy();
    const t = readChartTheme();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: rows.map((p) => p.as_of_date),
        datasets: [
          {
            label: 'Realised (council)',
            data: rows.map((p) => p.cum_realised_pct),
            borderColor: t.info,
            backgroundColor: t.info + '14',
            tension: 0.1, pointRadius: 0, borderWidth: 1.6, fill: false,
          },
          {
            label: 'Council-free baseline',
            data: rows.map((p) => p.cum_baseline_pct),
            borderColor: t.axis, borderDash: [5, 5],
            tension: 0.1, pointRadius: 0, borderWidth: 1.2, fill: false,
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: ENTRY_ANIMATION,
        plugins: { legend: baseLegend(t) },
        scales: {
          x: { display: false, grid: { color: t.grid } },
          y: {
            grid: { color: t.grid },
            ticks: {
              color: t.axis,
              font: { family: 'JetBrains Mono', size: 10 },
              callback: (v) => `${v}%`,
            },
          },
        },
      },
    };
    this.councilChart = new Chart(canvas, cfg);
  }

  openRun(id: number): void {
    window.location.assign(`/runs/${id}`);
  }

  recompute(): void {
    this.busy.set(true);
    this.api.post('/leaderboard/recompute/', {}).subscribe({
      next: () => {
        this.busy.set(false);
        this.load();
      },
      error: () => this.busy.set(false),
    });
  }

  pct(v: string | null): string {
    return v === null ? '—' : (Number(v) * 100).toFixed(1) + '%';
  }
  pctRaw(v: string | null): string {
    return v === null ? '—' : Number(v).toFixed(2) + '%';
  }
  num(v: string | null): string {
    return v === null ? '—' : Number(v).toFixed(2);
  }
  usd(v: string | null): string {
    return v === null ? '—' : '$' + Number(v).toFixed(4);
  }
  num0(v: string | null): number {
    return v === null ? 0 : Number(v);
  }
  iqr(lo: string | null, hi: string | null, suffix = ''): string {
    if (lo === null || hi === null) return '';
    return `IQR ${Number(lo).toFixed(2)}${suffix}–${Number(hi).toFixed(2)}${suffix}`;
  }
  contrarianTip(a: AgentRow): string {
    const lo = a.contrarian_hit_rate_ci_low;
    const hi = a.contrarian_hit_rate_ci_high;
    const ci = lo !== null && hi !== null ? ` (95% CI ${this.pct(lo)}–${this.pct(hi)})` : '';
    return (
      `Right ${this.pct(a.contrarian_hit_rate)} of the time across ` +
      `${a.n_contrarian_decisions} calls that dissented from the run consensus${ci}.`
    );
  }
  bps(v: string | null): string {
    if (v === null) return '—';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + Math.round(n).toLocaleString() + ' bps';
  }
  usd0(v: string | null): string {
    if (v === null) return '—';
    const n = Number(v);
    return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  /**
   * WAVE 3 — why this row's ratios are blank. `provisional === true` means the
   * backend deliberately returned `null` for sharpe / sortino /
   * annualised_return_pct; the fix is more measurable observations, never a
   * client-side estimate.
   */
  provTip(s: StrategyRow): string {
    const n = s.n_observations ?? 0;
    return (
      `Fewer than 20 measurable observations (${n} priced holding interval` +
      `${n === 1 ? '' : 's'} across ${s.n_cycles} cycle${s.n_cycles === 1 ? '' : 's'}). ` +
      'Sharpe, Sortino and annualised return are withheld — the sample cannot support them.'
    );
  }

  /** Tooltip for any ratio cell: the provisional reason, or the sample it rests on. */
  ratioTip(s: StrategyRow): string {
    if (s.provisional) return this.provTip(s);
    const ppy = s.periods_per_year ? `, annualised at ${Number(s.periods_per_year).toFixed(2)}/yr` : '';
    return `From ${s.n_observations} disjoint priced holding interval(s)${ppy}.`;
  }

  /** Sortino can be null on a NON-provisional row too — `sortino_note` says why. */
  sortinoTip(s: StrategyRow): string {
    if (s.sortino === null && s.sortino_note) return s.sortino_note;
    if (s.provisional) return this.provTip(s);
    return s.sortino_note || this.ratioTip(s);
  }

  councilTip(s: StrategyRow): string {
    return (
      `Annualised return vs the council-free baseline. ` +
      `Council net value ${this.usd0(s.council_net_value_usd)} ` +
      `(after ${this.usd0(s.council_cost_usd)} of LLM cost) · ` +
      `baseline ${s.baseline_version || 'n/a'}`
    );
  }
}
