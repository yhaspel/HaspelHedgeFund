import {
  AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild, effect, inject,
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
  imports: [CommonModule, DecimalPipe, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests', link:'/backtests'}, {label: store.current()?.name || ''}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Walk-forward backtest</div>
          <h1 class="mt-1.5">{{ store.current()?.name || 'Backtest' }}</h1>
        </div>
        <div class="head-actions">
          @if (store.current(); as bt) {
            <span class="pill"><span class="dot"></span>Run cost · $ {{ (bt.total_cost_usd || 0) | number: '1.2-2' }}</span>
            <a class="btn" [routerLink]="['/backtests', bt.id, 'compare']">Compare…</a>
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
              </div>
            </div>
            <div class="kpi">
              <div class="k">OOS Sharpe (mean)</div>
              <div class="v">{{ m.mean_oos_sharpe | number: '1.2-2' }}</div>
              <div class="d">
                <span class="delta"
                  [class.up]="m.mean_oos_sharpe > m.mean_is_sharpe"
                  [class.down]="m.mean_oos_sharpe < m.mean_is_sharpe"
                  [class.flat]="m.mean_oos_sharpe === m.mean_is_sharpe">
                  {{ m.mean_oos_sharpe > m.mean_is_sharpe ? '▲' : m.mean_oos_sharpe < m.mean_is_sharpe ? '▼' : '—' }}
                  {{ m.mean_oos_sharpe - m.mean_is_sharpe | number: '1.2-2' }}
                </span>
                vs IS {{ m.mean_is_sharpe | number: '1.2-2' }} · σ {{ m.oos_sharpe_std | number: '1.2-2' }}
              </div>
            </div>
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
            <div class="kpi">
              <div class="k">Max drawdown</div>
              <div class="v">{{ m.max_drawdown_pct | number: '1.2-2' }}%</div>
              <div class="d">Turnover ann.: {{ m.turnover_pct | number: '1.0-0' }}%</div>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-[18px] mb-[18px]">
            <section class="card">
              <div class="card-hd"><h2 class="title">Stitched OOS equity vs baseline ({{ bt.baseline }})</h2></div>
              <div class="card-bd"><div class="relative h-[260px]"><canvas #equityChart role="img"
                [attr.aria-label]="'Stitched out-of-sample equity curve vs the ' + bt.baseline + ' baseline for ' + bt.name + '. Portfolio OOS return ' + (bt.total_return_pct ?? 0) + '%, OOS Sharpe ' + (bt.oos_sharpe ?? 0) + '. See the Folds table below for the per-fold figures.'"></canvas></div></div>
            </section>
            <section class="card">
              <div class="card-hd"><h2 class="title">IS vs OOS Sharpe per fold</h2></div>
              <div class="card-bd"><div class="relative h-[260px]"><canvas #deflationChart role="img"
                aria-label="In-sample versus out-of-sample Sharpe ratio scatter, one point per walk-forward fold. Points near the diagonal indicate the in-sample edge held out of sample. Per-fold figures are in the Folds table below."></canvas></div></div>
            </section>
          </div>

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
  private viewReady = false;

  @ViewChild('equityChart', { static: false }) equityCanvas?: ElementRef<HTMLCanvasElement>;
  @ViewChild('deflationChart', { static: false }) deflationCanvas?: ElementRef<HTMLCanvasElement>;
  @ViewChild('attributionChart', { static: false }) attributionCanvas?: ElementRef<HTMLCanvasElement>;

  constructor() {
    effect(() => { this.store.equity(); if (this.viewReady) setTimeout(() => this.renderEquity(), 0); });
    effect(() => { this.store.deflation(); if (this.viewReady) setTimeout(() => this.renderDeflation(), 0); });
    effect(() => { this.store.current(); if (this.viewReady) setTimeout(() => this.renderAttribution(), 0); });
  }

  ngOnInit(): void {
    this.id = Number(this.route.snapshot.paramMap.get('id'));
    this.store.poll(this.id, 3000);
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
    let n = 0;
    const tick = () => {
      this.renderCharts();
      if (++n < 12) setTimeout(tick, 250);
    };
    setTimeout(tick, 100);
  }

  private id = 0;

  ngOnDestroy(): void {
    this.store.stopPolling();
    this.equityChartInstance?.destroy();
    this.deflationChartInstance?.destroy();
    this.attributionChartInstance?.destroy();
  }

  private renderCharts(): void {
    if (!this.viewReady) return;
    this.renderEquity();
    this.renderDeflation();
    this.renderAttribution();
  }

  private renderEquity(): void {
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
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: ENTRY_ANIMATION,
        plugins: { legend: baseLegend(t) },
        scales: {
          x: { display: false, grid: { color: t.grid } },
          y: { grid: { color: t.grid }, ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } } },
        },
      },
    };
    this.equityChartInstance = new Chart(canvas, cfg);
  }

  private renderDeflation(): void {
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

  private renderAttribution(): void {
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
