import {
  AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild, computed, effect, inject,
  signal,
} from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import {
  Chart, ChartConfiguration, LineController, LineElement, PointElement,
  CategoryScale, LinearScale, Tooltip, Legend, Title, ScatterController,
  BarController, BarElement,
} from 'chart.js';
import { AppShellComponent } from '../shared/app-shell.component';
import { ConfirmService } from '../shared/confirm.service';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { engineVersionBadge, engineVersionLabel } from '../../core/models/backtest.model';
import { ErrorStateComponent } from '../shared/error-state.component';
import { BacktestComparePanelComponent } from './backtest-compare-panel.component';
import { Subscription } from 'rxjs';
import { ENTRY_ANIMATION, baseLegend, personaColorById, readChartTheme } from '../shared/chart-defaults';

Chart.register(
  LineController, LineElement, PointElement, CategoryScale, LinearScale,
  Tooltip, Legend, Title, ScatterController, BarController, BarElement,
);

const DELETABLE_BACKTEST_STATUSES = new Set([
  'cancelled', 'aborted_budget', 'aborted_partial', 'synthetic',
]);

@Component({
  selector: 'hf-backtests-detail',
  standalone: true,
  imports: [
    CommonModule, DecimalPipe, RouterLink, AppShellComponent, ErrorStateComponent,
    BacktestComparePanelComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests', link:'/backtests'}, {label: store.current()?.name || ''}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Walk-forward backtest</div>
          <h1 class="mt-1.5">{{ store.current()?.name || 'Backtest' }}
            @if (store.current()?.data_era === 'price_only') {
              <span class="pill warn" title="Computed on pre-PR#50 price-only bars (dividends not credited) — understates returns; excluded as §9 autopilot-gate evidence."><span class="dot"></span>price-only era</span>
            }
            @if (store.current()?.status === 'synthetic') {
              <span class="pill warn" title="Fabricated demo record — not a real engine run; never §9-gate evidence."><span class="dot"></span>synthetic</span>
            }
            @if (store.current(); as bt) {
              <span class="pill" data-test="engine-version"
                    [title]="engineLabel(bt.engine_version)"><span class="dot"></span>{{ engineBadge(bt.engine_version) }}</span>
            }
          </h1>
        </div>
        <div class="head-actions">
          @if (store.current(); as bt) {
            <span class="pill"><span class="dot"></span>Run cost · $ {{ (bt.total_cost_usd || 0) | number: '1.2-2' }}</span>
            <!-- WAVE 3 item 9: comparison is a panel on THIS page now, not a
                 separate bare route. /backtests/:id/compare still resolves and
                 redirects here, so existing links keep working. -->
            <button type="button" class="btn" (click)="toggleCompare()"
                    [attr.aria-expanded]="compareOpen()"
                    aria-controls="backtest-compare-panel"
                    data-test="open-compare">
              {{ compareOpen() ? 'Hide comparison' : 'Compare…' }}
            </button>
            @if (bt.status === 'running' || bt.status === 'queued') {
              <button type="button" class="btn text-[color:var(--acc-short-fg)] border-[color:var(--acc-short-soft)]"
                [disabled]="cancelling"
                (click)="cancelRun(bt.id)"
                data-test="cancel-backtest">
                {{ cancelling ? 'Cancelling…' : 'Cancel run' }}
              </button>
            }
            @if (canDelete(bt.status)) {
              <button type="button" class="btn danger"
                [disabled]="deleting"
                (click)="confirmDelete(bt.id, bt.name)"
                data-test="delete-backtest">
                {{ deleting ? 'Deleting…' : 'Delete' }}
              </button>
            }
          }
          <a class="btn ghost" routerLink="/backtests">Back to list</a>
        </div>
      </div>

      @if (store.current(); as bt) {
        @if (bt.status !== 'done') {
          <section class="card mb-[18px] border-[var(--acc-hold-soft)] bg-[var(--acc-hold-soft)]">
            <div class="card-bd">
              <div class="flex justify-between items-center">
                <div class="text-xs">
                  <span class="font-semibold">Status:</span> {{ bt.status }}
                  @if (bt.progress_message) {
                    <span class="text-text-2"> — {{ bt.progress_message }}</span>
                  }
                </div>
                <div class="mono text-2xs text-text-2">{{ bt.progress_pct }}%</div>
              </div>
              <div class="cost-gauge mt-2 h-2"><div class="fill bg-hold" [style.width.%]="bt.progress_pct"></div></div>
              @if (bt.error_message) {
                <p class="text-[var(--acc-short-fg)] text-2xs m-0 mt-2">{{ bt.error_message }}</p>
              }
            </div>
          </section>
        }

        @if (bt.metrics; as m) {
          <div class="grid grid-cols-4 gap-3.5 mb-[18px]">
            <div class="kpi">
              <div class="k">Stitched OOS return</div>
              <div class="v">{{ m.total_return_pct | number: '1.2-2' }}%</div>
              <div class="d">
                <span class="delta"
                  [class.up]="m.total_return_pct > m.baseline_return_pct"
                  [class.down]="m.total_return_pct < m.baseline_return_pct"
                  [class.flat]="m.total_return_pct === m.baseline_return_pct">
                  {{ m.total_return_pct > m.baseline_return_pct ? '▲' : m.total_return_pct < m.baseline_return_pct ? '▼' : '—' }}
                  {{ m.total_return_pct - m.baseline_return_pct | number: '1.2-2' }}
                </span>
                vs baseline {{ m.baseline_return_pct | number: '1.2-2' }}%
                @if (m.baseline_return_pct_legacy !== null) {
                  <span class="text-text-2" title="The baseline was recomputed as a total-return, true equal-weight basket (P10 §B1); this was the old price-only, price-weighted value.">
                    (was {{ m.baseline_return_pct_legacy | number: '1.2-2' }}%)
                  </span>
                }
              </div>
            </div>
            <div class="kpi">
              <div class="k">OOS Sharpe (stitched)</div>
              <div class="v">{{ m.sharpe | number: '1.2-2' }}</div>
              <div class="d" title="The stitched number is the long-run Sharpe of the whole OOS curve; averaging 63-day fold Sharpes biases ~0.3–0.4 upward.">
                mean of folds {{ m.mean_oos_sharpe | number: '1.2-2' }}
                · IS {{ m.mean_is_sharpe | number: '1.2-2' }}
                · σ {{ m.oos_sharpe_std | number: '1.2-2' }}
              </div>
            </div>
            @if (bt.deflation_meaningful) {
              <div class="kpi">
                <div class="k">Deflation (OOS / IS)</div>
                <div class="v"
                  [style.color]="m.sharpe_deflation < 0.3 ? 'var(--acc-short-fg)' : m.sharpe_deflation < 0.5 ? 'var(--acc-hold-fg)' : 'var(--acc-long-fg)'">
                  {{ m.sharpe_deflation | number: '1.2-2' }}
                  <span aria-hidden="true"> {{ m.sharpe_deflation < 0.3 ? '⚠' : m.sharpe_deflation < 0.5 ? '•' : '✓' }}</span>
                </div>
                @if (m.sharpe_deflation < 0.3) {
                  <div class="d text-[var(--acc-short-fg)]">⚠ Strong overfitting suspected</div>
                }
              </div>
            } @else {
              <div class="kpi">
                <div class="k">Deflation (OOS / IS)</div>
                <div class="v text-text-2">n/a</div>
                <div class="d" title="The OOS/IS ratio only guards against overfitting when an in-sample candidate search selected a config. This run replays one fixed config, so the ratio is not an overfit signal.">
                  No candidate search (deterministic run)
                </div>
              </div>
            }
            <div class="kpi">
              <div class="k">Max drawdown</div>
              <div class="v">{{ m.max_drawdown_pct | number: '1.2-2' }}%</div>
              <div class="d">Turnover ann.: {{ m.turnover_pct | number: '1.0-0' }}%</div>
            </div>
          </div>

          <!-- P10 §B2: benchmark-relative truth — beta / CAPM alpha / IR vs
               SPY-TR & QQQ-TR. Rendered only when the benchmark block exists
               (post-recompute or fresh runs with benchmark bars). -->
          @if (benchmarkRows(m).length) {
            <section class="card mb-[18px]">
              <div class="card-hd"><h2 class="title">vs benchmarks (total-return)</h2></div>
              <table class="tbl">
                <thead><tr>
                  <th>Benchmark</th>
                  <th class="right">Benchmark return</th><th class="right">Benchmark Sharpe</th>
                  <th class="right">Benchmark max DD</th><th class="right">Beta</th>
                  <th class="right">CAPM alpha /yr</th><th class="right">Info ratio</th>
                </tr></thead>
                <tbody>
                  @for (row of benchmarkRows(m); track row.ticker) {
                    <tr>
                      <td class="mono">{{ row.ticker }}-TR</td>
                      <td class="num">{{ row.b.total_return_pct | number: '1.1-1' }}%</td>
                      <td class="num">{{ row.b.sharpe | number: '1.2-2' }}</td>
                      <td class="num">{{ row.b.max_drawdown_pct | number: '1.1-1' }}%</td>
                      <td class="num">{{ row.b.beta !== undefined ? (row.b.beta | number: '1.2-2') : '—' }}</td>
                      <td class="num"
                        [style.color]="(row.b.alpha_annual_pct ?? 0) >= 0 ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                        {{ row.b.alpha_annual_pct !== undefined ? ((row.b.alpha_annual_pct | number: '1.2-2') + '%') : '—' }}
                      </td>
                      <td class="num">{{ row.b.information_ratio !== undefined ? (row.b.information_ratio | number: '1.2-2') : '—' }}</td>
                    </tr>
                  }
                </tbody>
              </table>
              <div class="card-bd pt-0 text-2xs text-text-2">
                Alpha here is CAPM alpha (return unexplained by benchmark beta), annualized.
                A positive alpha with a raw-return gap means the edge is risk-adjusted, not absolute-return.
              </div>
            </section>
          }

          <div class="grid grid-cols-2 gap-[18px] mb-[18px]">
            <section class="card">
              <div class="card-hd"><h2 class="title">Stitched OOS equity vs baseline ({{ bt.baseline }})</h2></div>
              <div class="card-bd"><div class="relative h-[260px]"><canvas #equityChart role="img"
                [attr.aria-label]="equityChartLabel()"></canvas></div></div>
            </section>
            <section class="card">
              <div class="card-hd"><h2 class="title">IS vs OOS Sharpe per fold</h2></div>
              <div class="card-bd"><div class="relative h-[260px]"><canvas #deflationChart role="img"
                aria-label="In-sample versus out-of-sample Sharpe ratio scatter, one point per walk-forward fold. Points near the diagonal indicate the in-sample edge held out of sample. Per-fold figures are in the Folds table below."></canvas></div></div>
            </section>
          </div>

          @if (store.rollingSharpe().length) {
            <section class="card mb-[18px]">
              <div class="card-hd"><h2 class="title">Rolling 3-year Sharpe (stitched OOS)</h2></div>
              <div class="card-bd"><div class="relative h-[160px]"><canvas #rollingChart role="img"
                aria-label="Rolling three-year Sharpe ratio of the stitched out-of-sample curve, sampled monthly. Persistently positive values indicate the edge held across regimes."></canvas></div></div>
            </section>
          }

          <section class="card mb-[18px]">
            <div class="card-hd"><h2 class="title">Per-agent attribution (stitched OOS PnL delta)</h2></div>
            <div class="card-bd"><div class="relative h-[240px]"><canvas #attributionChart role="img"
              aria-label="Per-agent attribution bar chart: each bar is one council agent's contribution to the stitched out-of-sample PnL delta, positive bars added return and negative bars detracted."></canvas></div></div>
          </section>
        }

        <section class="card">
          <div class="card-hd"><h2 class="title">Folds</h2></div>
          <table class="tbl">
            <thead><tr>
              <th>#</th><th>IS range</th><th>OOS range</th>
              <th class="right">IS Sharpe</th><th class="right">OOS Sharpe</th>
              <th class="right">OOS return</th><th class="right">OOS max DD</th>
            </tr></thead>
            <tbody>
              @for (f of bt.folds; track f.id) {
                <tr>
                  <td class="mono">{{ f.fold_index }}</td>
                  <td class="mono text-text-2">{{ f.is_start }} → {{ f.is_end }}</td>
                  <td class="mono text-text-2">{{ f.oos_start }} → {{ f.oos_end }}</td>
                  <td class="num">{{ f.is_sharpe | number: '1.2-2' }}</td>
                  <td class="num">{{ f.oos_sharpe | number: '1.2-2' }}</td>
                  <td class="num">{{ f.oos_return_pct | number: '1.2-2' }}%</td>
                  <td class="num">{{ f.oos_max_drawdown_pct | number: '1.2-2' }}%</td>
                </tr>
              }
            </tbody>
          </table>
        </section>
      } @else if (store.pollError()) {
        <hf-error-state
          title="Couldn't load this backtest"
          [detail]="store.pollError()"
          (retry)="retryLoad()"
        ></hf-error-state>
      } @else {
        <p class="text-text-3">Loading…</p>
      }

      @if (compareOpen() && id) {
        <div id="backtest-compare-panel" class="mt-[18px]">
          <hf-backtest-compare-panel
            [backtestId]="id"
            [initialCompareId]="compareWith()"
            (closed)="closeCompare()"
          ></hf-backtest-compare-panel>
        </div>
      }
    </hf-app-shell>
  `,
})
export class BacktestsDetailPage implements OnInit, OnDestroy, AfterViewInit {
  readonly store = inject(BacktestsStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly confirm = inject(ConfirmService);
  private equityChartInstance: Chart | null = null;
  private deflationChartInstance: Chart | null = null;
  private attributionChartInstance: Chart | null = null;
  private rollingChartInstance: Chart | null = null;
  private viewReady = false;

  @ViewChild('equityChart', { static: false }) equityCanvas?: ElementRef<HTMLCanvasElement>;
  @ViewChild('deflationChart', { static: false }) deflationCanvas?: ElementRef<HTMLCanvasElement>;
  @ViewChild('attributionChart', { static: false }) attributionCanvas?: ElementRef<HTMLCanvasElement>;
  @ViewChild('rollingChart', { static: false }) rollingCanvas?: ElementRef<HTMLCanvasElement>;

  constructor() {
    effect(() => { this.store.equity(); if (this.viewReady) setTimeout(() => this.renderEquity(), 0); });
    effect(() => { this.store.deflation(); if (this.viewReady) setTimeout(() => this.renderDeflation(), 0); });
    effect(() => { this.store.current(); if (this.viewReady) setTimeout(() => this.renderAttribution(), 0); });
    effect(() => { this.store.rollingSharpe(); if (this.viewReady) setTimeout(() => this.renderRolling(), 0); });
  }

  /**
   * Screen-reader description of the equity chart.
   *
   * It used to read `bt.total_return_pct` / `bt.oos_sharpe` — LIST serializer
   * fields that the detail payload leaves null — so every backtest announced
   * "OOS return 0%, OOS Sharpe 0" no matter what it did. The computed metrics
   * block is the authoritative source; the list fields are only a fallback.
   */
  readonly equityChartLabel = computed(() => {
    const bt = this.store.current();
    if (!bt) return 'Stitched out-of-sample equity curve.';
    const m = bt.metrics;
    // The computed-metrics block serialises DecimalFields as STRINGS ("18.5000"),
    // so these have to be coerced before toFixed — calling it on a string throws
    // inside the computed and the canvas silently loses its accessible name.
    const asNumber = (v: unknown): number =>
      v === null || v === undefined ? NaN : Number(v);
    const ret = asNumber(m?.total_return_pct ?? bt.total_return_pct);
    const sharpe = asNumber(m?.sharpe ?? bt.stitched_sharpe ?? bt.oos_sharpe);
    const numbers =
      !Number.isFinite(ret) || !Number.isFinite(sharpe)
        ? 'Results are not available yet.'
        : `Portfolio OOS return ${ret.toFixed(2)}%, stitched OOS Sharpe ${sharpe.toFixed(2)}.`;
    const engine = engineVersionLabel(bt.engine_version);
    return (
      `Stitched out-of-sample equity curve vs the ${bt.baseline} baseline for ${bt.name}. ` +
      `${numbers} ${engine} See the Folds table below for the per-fold figures.`
    );
  });

  readonly engineLabel = engineVersionLabel;
  readonly engineBadge = engineVersionBadge;

  // P10 §B2: ordered benchmark rows from the metrics JSON ({} for old rows).
  benchmarkRows(m: { benchmarks?: Record<string, any> }): { ticker: string; b: any }[] {
    const bench = m?.benchmarks || {};
    return ['SPY', 'QQQ']
      .filter((t) => !!bench[t])
      .map((t) => ({ ticker: t, b: bench[t] }));
  }

  /**
   * The component instance survives /backtests/1 → /backtests/2 (default
   * RouteReuseStrategy), so reading `route.snapshot` once left the previous
   * backtest on screen under the new url. Follow paramMap instead.
   */
  /**
   * WAVE 3 item 9 — the compare panel's open state.
   *
   * `?compare=1` opens it empty (what the folded-in `/…/compare` route sends);
   * `?compare=<id>` opens it with B pre-selected, so an old deep link that
   * named a specific pair still lands on the same comparison.
   */
  readonly compareOpen = signal(false);
  readonly compareWith = signal<number | null>(null);

  toggleCompare(): void {
    this.compareOpen.set(!this.compareOpen());
  }

  closeCompare(): void {
    this.compareOpen.set(false);
    this.compareWith.set(null);
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { compare: null },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  ngOnInit(): void {
    this.paramSub = this.route.paramMap.subscribe((params) => {
      const id = Number(params.get('id'));
      if (!id || id === this.id) return;
      this.id = id;
      this.destroyCharts();
      this.store.poll(this.id, 3000);
      if (this.viewReady) this.startChartWarmup();
    });
    // `?.` because a test double / a route without query params has no
    // `queryParamMap` — the compare panel is optional, the page is not.
    this.querySub = this.route.queryParamMap?.subscribe((q) => {
      const raw = q.get('compare');
      if (raw === null) {
        this.compareOpen.set(false);
        this.compareWith.set(null);
        return;
      }
      this.compareOpen.set(true);
      const b = Number(raw);
      this.compareWith.set(Number.isFinite(b) && b > 1 ? b : null);
    });
  }

  retryLoad(): void {
    if (this.id) this.store.poll(this.id, 3000);
  }

  cancelling = false;

  async cancelRun(id: number): Promise<void> {
    // Interstitial dialog — the action is reversible only by submitting a new
    // run, so a one-tap gate is worth the friction. Cancellation surfaces
    // as `status='cancelled'` on the next poll tick (3s window) and the
    // button hides itself via the @if guard above.
    const ok = await this.confirm.ask({
      title: 'Cancel this backtest?',
      body: 'Work already done will be lost.',
      confirmLabel: 'Cancel backtest',
      cancelLabel: 'Keep running',
      danger: true,
    });
    if (!ok) return;
    this.cancelling = true;
    this.store.cancel(id).subscribe({
      next: () => { this.cancelling = false; },
      error: () => { this.cancelling = false; },
    });
  }

  // P4 WS-D: delete a cancelled / aborted / synthetic backtest, then return
  // to the list. done + failed never show the button (protected history).
  deleting = false;
  canDelete(status: string): boolean {
    return DELETABLE_BACKTEST_STATUSES.has(status);
  }
  async confirmDelete(id: number, name: string): Promise<void> {
    if (this.deleting) return;
    const ok = await this.confirm.ask({
      title: `Delete backtest "${name}"?`,
      body: 'This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    this.deleting = true;
    this.store.deleteBacktest(id).subscribe({
      next: () => { this.deleting = false; this.router.navigate(['/backtests']); },
      error: () => { this.deleting = false; void this.confirm.notify({ title: 'Could not delete this backtest.' }); },
    });
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    this.startChartWarmup();
  }

  /**
   * Canvas elements appear behind @if blocks as the data arrives, so the charts
   * are re-attempted for ~3 s. The handle is kept (and `destroyed` checked)
   * because otherwise those timeouts kept firing after ngOnDestroy and called
   * `new Chart()` on detached canvases — leaking a Chart instance per tick and
   * throwing once the element was gone.
   */
  private startChartWarmup(): void {
    if (this.warmupHandle) clearTimeout(this.warmupHandle);
    let n = 0;
    const tick = () => {
      this.warmupHandle = null;
      if (this.destroyed) return;
      this.renderCharts();
      if (++n < 12) this.warmupHandle = setTimeout(tick, 250);
    };
    this.warmupHandle = setTimeout(tick, 100);
  }

  // `id` is read by the template (the compare panel's A side), so it cannot
  // stay private.
  id = 0;
  private paramSub: Subscription | null = null;
  private querySub: Subscription | null = null;
  private warmupHandle: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;

  ngOnDestroy(): void {
    this.destroyed = true;
    if (this.warmupHandle) clearTimeout(this.warmupHandle);
    this.warmupHandle = null;
    this.paramSub?.unsubscribe();
    this.paramSub = null;
    this.querySub?.unsubscribe();
    this.querySub = null;
    this.store.stopPolling();
    this.destroyCharts();
  }

  private destroyCharts(): void {
    this.equityChartInstance?.destroy();
    this.equityChartInstance = null;
    this.deflationChartInstance?.destroy();
    this.deflationChartInstance = null;
    this.attributionChartInstance?.destroy();
    this.attributionChartInstance = null;
    this.rollingChartInstance?.destroy();
    this.rollingChartInstance = null;
  }

  private renderCharts(): void {
    if (!this.viewReady || this.destroyed) return;
    this.renderEquity();
    this.renderDeflation();
    this.renderAttribution();
    this.renderRolling();
  }

  private renderEquity(): void {
    if (this.destroyed) return;
    const points = this.store.equity();
    const canvas = this.equityCanvas?.nativeElement;
    if (!canvas || points.length === 0) return;
    this.equityChartInstance?.destroy();
    const t = readChartTheme();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: points.map((p) => p.date),
        datasets: [
          {
            label: 'Portfolio (OOS stitched)',
            data: points.map((p) => p.portfolio_value),
            borderColor: t.info,
            backgroundColor: t.info + '14',
            tension: 0.1, pointRadius: 0, borderWidth: 1.4,
          },
          {
            label: 'Baseline',
            data: points.map((p) => p.baseline ?? null),
            borderColor: t.axis, borderDash: [5, 5],
            tension: 0.1, pointRadius: 0, borderWidth: 1,
          },
          // P10 §B2: SPY-TR / QQQ-TR overlays (skipped when absent).
          ...(points.some((p) => p.spy !== undefined) ? [{
            label: 'SPY (TR)',
            data: points.map((p) => p.spy ?? null),
            borderColor: t.long, borderDash: [2, 3],
            tension: 0.1, pointRadius: 0, borderWidth: 1,
          }] : []),
          ...(points.some((p) => p.qqq !== undefined) ? [{
            label: 'QQQ (TR)',
            data: points.map((p) => p.qqq ?? null),
            borderColor: t.short, borderDash: [2, 3],
            tension: 0.1, pointRadius: 0, borderWidth: 1,
          }] : []),
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: ENTRY_ANIMATION,
        plugins: { legend: baseLegend(t) },
        scales: {
          // An equity curve with a hidden x axis is unreadable: nothing says
          // which years the drawdown happened in. Show the dates (thinned so a
          // multi-year daily series stays legible).
          x: {
            display: true,
            grid: { color: t.grid },
            ticks: {
              color: t.axis,
              font: { family: 'JetBrains Mono', size: 10 },
              autoSkip: true,
              maxTicksLimit: 8,
              maxRotation: 0,
              callback: (_v, index) => points[index]?.date ?? '',
            },
          },
          y: { grid: { color: t.grid }, ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } } },
        },
      },
    };
    this.equityChartInstance = new Chart(canvas, cfg);
  }

  private renderDeflation(): void {
    if (this.destroyed) return;
    const d = this.store.deflation();
    const canvas = this.deflationCanvas?.nativeElement;
    if (!canvas || !d || d.per_fold.length === 0) return;
    this.deflationChartInstance?.destroy();
    const t = readChartTheme();
    const cfg: ChartConfiguration = {
      type: 'scatter',
      data: {
        datasets: [{
          label: 'Folds (IS vs OOS Sharpe)',
          data: d.per_fold.map((p) => ({ x: p.is_sharpe, y: p.oos_sharpe })),
          backgroundColor: t.info,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: ENTRY_ANIMATION,
        plugins: { legend: baseLegend(t) },
        scales: {
          x: { title: { display: true, text: 'IS Sharpe', color: t.axis }, grid: { color: t.grid }, ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } } },
          y: { title: { display: true, text: 'OOS Sharpe', color: t.axis }, grid: { color: t.grid }, ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } } },
        },
      },
    };
    this.deflationChartInstance = new Chart(canvas, cfg);
  }

  // P10 §B2: rolling ~3y Sharpe sparkline of the stitched OOS curve.
  private renderRolling(): void {
    if (this.destroyed) return;
    const series = this.store.rollingSharpe();
    const canvas = this.rollingCanvas?.nativeElement;
    if (!canvas || series.length === 0) return;
    this.rollingChartInstance?.destroy();
    const t = readChartTheme();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: series.map((p) => p.date),
        datasets: [{
          label: 'Rolling 3y Sharpe',
          data: series.map((p) => p.sharpe),
          borderColor: t.info,
          backgroundColor: t.info + '14',
          tension: 0.15, pointRadius: 0, borderWidth: 1.2, fill: 'origin',
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: ENTRY_ANIMATION,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            display: true,
            grid: { color: t.grid },
            ticks: {
              color: t.axis,
              font: { family: 'JetBrains Mono', size: 10 },
              autoSkip: true,
              maxTicksLimit: 6,
              maxRotation: 0,
              callback: (_v, index) => series[index]?.date ?? '',
            },
          },
          y: { grid: { color: t.grid }, ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } } },
        },
      },
    };
    this.rollingChartInstance = new Chart(canvas, cfg);
  }

  private renderAttribution(): void {
    if (this.destroyed) return;
    const bt = this.store.current();
    const canvas = this.attributionCanvas?.nativeElement;
    if (!canvas || !bt?.metrics) return;
    const attr = bt.metrics.per_agent_attribution || {};
    const labels = Object.keys(attr);
    if (labels.length === 0) return;
    this.attributionChartInstance?.destroy();
    const t = readChartTheme();
    const cfg: ChartConfiguration = {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'PnL delta if agent neutralized',
          data: labels.map((l) => attr[l]),
          backgroundColor: labels.map((l) => {
            const v = attr[l];
            if (v === 0) return t.axis;
            const personaColor = personaColorById(l);
            return v >= 0 ? personaColor : t.short;
          }),
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: ENTRY_ANIMATION,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } }, grid: { color: t.grid } },
          y: { title: { display: true, text: 'USD', color: t.axis }, grid: { color: t.grid }, ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } } },
        },
      },
    };
    this.attributionChartInstance = new Chart(canvas, cfg);
  }
}
