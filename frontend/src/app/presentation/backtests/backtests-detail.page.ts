import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  effect,
  inject,
} from '@angular/core';
import { DecimalPipe, NgClass } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  Chart,
  ChartConfiguration,
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Title,
  ScatterController,
  BarController,
  BarElement,
} from 'chart.js';
import { BacktestsStore } from '../../abstraction/backtests.store';

Chart.register(
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Title,
  ScatterController,
  BarController,
  BarElement,
);

@Component({
  selector: 'hf-backtests-detail',
  standalone: true,
  imports: [DecimalPipe, NgClass, RouterLink],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-semibold">
          {{ store.current()?.name || 'Backtest' }}
        </h1>
        <div class="flex items-center gap-3 text-sm">
          @if (store.current(); as bt) {
            <span class="text-gray-600">Run cost:
              <span class="font-medium">\${{ (bt.total_cost_usd || 0) | number: '1.2-2' }}</span>
            </span>
            <a [routerLink]="['/backtests', bt.id, 'compare']"
               class="text-blue-600 hover:underline">Compare…</a>
          }
          <a routerLink="/backtests" class="text-blue-600 hover:underline">Back to list</a>
        </div>
      </header>

      @if (store.current(); as bt) {
        @if (bt.status !== 'done') {
          <div class="bg-yellow-50 border border-yellow-200 rounded p-4 mb-6">
            <div class="flex items-center justify-between">
              <div>
                <span class="font-medium">Status:</span> {{ bt.status }}
                @if (bt.progress_message) {
                  <span class="text-sm text-gray-600"> — {{ bt.progress_message }}</span>
                }
              </div>
              <div class="text-sm text-gray-700">{{ bt.progress_pct }}%</div>
            </div>
            <div class="w-full bg-yellow-100 h-2 rounded mt-2">
              <div class="bg-yellow-600 h-2 rounded" [style.width.%]="bt.progress_pct"></div>
            </div>
            @if (bt.error_message) {
              <p class="text-red-700 text-sm mt-2">{{ bt.error_message }}</p>
            }
          </div>
        }

        @if (bt.metrics; as m) {
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-white p-4 rounded shadow">
              <div class="text-xs text-gray-500">Stitched OOS return</div>
              <div class="text-xl font-semibold">{{ m.total_return_pct | number: '1.2-2' }}%</div>
              <div class="text-xs text-gray-400 mt-1">
                Baseline: {{ m.baseline_return_pct | number: '1.2-2' }}%
              </div>
            </div>
            <div class="bg-white p-4 rounded shadow">
              <div class="text-xs text-gray-500">OOS Sharpe (mean)</div>
              <div class="text-xl font-semibold">{{ m.mean_oos_sharpe | number: '1.2-2' }}</div>
              <div class="text-xs text-gray-400 mt-1">
                IS mean: {{ m.mean_is_sharpe | number: '1.2-2' }} · σ: {{ m.oos_sharpe_std | number: '1.2-2' }}
              </div>
            </div>
            <div class="bg-white p-4 rounded shadow">
              <div class="text-xs text-gray-500">Deflation (OOS / IS)</div>
              <div
                class="text-xl font-semibold"
                [ngClass]="{
                  'text-red-600': m.sharpe_deflation < 0.3,
                  'text-yellow-700': m.sharpe_deflation >= 0.3 && m.sharpe_deflation < 0.5,
                  'text-green-700': m.sharpe_deflation >= 0.5,
                }"
              >
                {{ m.sharpe_deflation | number: '1.2-2' }}
              </div>
              @if (m.sharpe_deflation < 0.3) {
                <div class="text-xs text-red-600 mt-1">⚠ Strong overfitting suspected</div>
              }
            </div>
            <div class="bg-white p-4 rounded shadow">
              <div class="text-xs text-gray-500">Max drawdown / Turnover</div>
              <div class="text-xl font-semibold">{{ m.max_drawdown_pct | number: '1.2-2' }}%</div>
              <div class="text-xs text-gray-400 mt-1">
                Turnover (ann.): {{ m.turnover_pct | number: '1.0-0' }}%
              </div>
            </div>
          </div>

          <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <div class="bg-white p-4 rounded shadow">
              <h3 class="text-sm font-semibold mb-2">Stitched OOS equity curve vs baseline ({{ bt.baseline }})</h3>
              <div style="position:relative; height:260px;"><canvas #equityChart></canvas></div>
            </div>
            <div class="bg-white p-4 rounded shadow">
              <h3 class="text-sm font-semibold mb-2">IS vs OOS Sharpe per fold</h3>
              <div style="position:relative; height:260px;"><canvas #deflationChart></canvas></div>
            </div>
          </div>

          <div class="bg-white p-4 rounded shadow mb-6">
            <h3 class="text-sm font-semibold mb-2">Per-agent attribution (stitched OOS PnL delta)</h3>
            <div style="position:relative; height:240px;"><canvas #attributionChart></canvas></div>
          </div>
        }

        <div class="bg-white rounded shadow overflow-hidden mb-6">
          <h3 class="text-sm font-semibold p-4 border-b">Folds</h3>
          <table class="min-w-full text-sm">
            <thead class="bg-gray-100 text-left">
              <tr>
                <th class="px-3 py-2">#</th>
                <th class="px-3 py-2">IS range</th>
                <th class="px-3 py-2">OOS range</th>
                <th class="px-3 py-2 text-right">IS Sharpe</th>
                <th class="px-3 py-2 text-right">OOS Sharpe</th>
                <th class="px-3 py-2 text-right">OOS return</th>
                <th class="px-3 py-2 text-right">OOS max DD</th>
              </tr>
            </thead>
            <tbody>
              @for (f of bt.folds; track f.id) {
                <tr class="border-t">
                  <td class="px-3 py-2">{{ f.fold_index }}</td>
                  <td class="px-3 py-2 text-gray-700">{{ f.is_start }} → {{ f.is_end }}</td>
                  <td class="px-3 py-2 text-gray-700">{{ f.oos_start }} → {{ f.oos_end }}</td>
                  <td class="px-3 py-2 text-right">{{ f.is_sharpe | number: '1.2-2' }}</td>
                  <td class="px-3 py-2 text-right">{{ f.oos_sharpe | number: '1.2-2' }}</td>
                  <td class="px-3 py-2 text-right">{{ f.oos_return_pct | number: '1.2-2' }}%</td>
                  <td class="px-3 py-2 text-right">{{ f.oos_max_drawdown_pct | number: '1.2-2' }}%</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    </div>
  `,
})
export class BacktestsDetailPage implements OnInit, OnDestroy, AfterViewInit {
  readonly store = inject(BacktestsStore);
  private readonly route = inject(ActivatedRoute);
  private equityChartInstance: Chart | null = null;
  private deflationChartInstance: Chart | null = null;
  private attributionChartInstance: Chart | null = null;
  private viewReady = false;

  @ViewChild('equityChart', { static: false }) equityCanvas?: ElementRef<HTMLCanvasElement>;
  @ViewChild('deflationChart', { static: false }) deflationCanvas?: ElementRef<HTMLCanvasElement>;
  @ViewChild('attributionChart', { static: false }) attributionCanvas?: ElementRef<HTMLCanvasElement>;

  constructor() {
    // Three independent effects so each chart re-renders only when its own
    // data signal changes (avoids tearing down all 3 charts on every tick).
    effect(() => {
      const _e = this.store.equity();
      if (this.viewReady) setTimeout(() => this.renderEquity(), 0);
    });
    effect(() => {
      const _d = this.store.deflation();
      if (this.viewReady) setTimeout(() => this.renderDeflation(), 0);
    });
    effect(() => {
      const _c = this.store.current();
      if (this.viewReady) setTimeout(() => this.renderAttribution(), 0);
    });
  }

  ngOnInit(): void {
    this.id = Number(this.route.snapshot.paramMap.get('id'));
    this.store.poll(this.id, 3000);
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    // After the view is in the DOM, kick a render cycle each time data arrives.
    // We can't rely on effect() alone — Chart.js needs the canvas to be sized
    // by layout first, which happens after the template re-renders. A 250ms
    // tick that runs for 3s catches all data-arrival races deterministically.
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
    const canvas = (this.equityCanvas?.nativeElement
      ?? (document.querySelector('canvas') as HTMLCanvasElement | null));
    if (!canvas || points.length === 0) return;
    this.equityChartInstance?.destroy();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: points.map((p) => p.date),
        datasets: [
          {
            label: 'Portfolio (OOS stitched)',
            data: points.map((p) => p.portfolio_value),
            borderColor: '#2563eb',
            backgroundColor: 'rgba(37,99,235,0.08)',
            tension: 0.1,
            pointRadius: 0,
          },
          {
            label: 'Baseline',
            data: points.map((p) => p.baseline ?? null),
            borderColor: '#9ca3af',
            borderDash: [5, 5],
            tension: 0.1,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: { x: { display: false } },
      },
    };
    this.equityChartInstance = new Chart(canvas, cfg);
  }

  private renderDeflation(): void {
    const d = this.store.deflation();
    const canvas = (this.deflationCanvas?.nativeElement
      ?? (document.querySelectorAll('canvas')[1] as HTMLCanvasElement | null));
    if (!canvas || !d || d.per_fold.length === 0) return;
    this.deflationChartInstance?.destroy();
    const cfg: ChartConfiguration = {
      type: 'scatter',
      data: {
        datasets: [
          {
            label: 'Folds (IS vs OOS Sharpe)',
            data: d.per_fold.map((p) => ({ x: p.is_sharpe, y: p.oos_sharpe })),
            backgroundColor: '#2563eb',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { title: { display: true, text: 'IS Sharpe' } },
          y: { title: { display: true, text: 'OOS Sharpe' } },
        },
        plugins: { legend: { position: 'bottom' } },
      },
    };
    this.deflationChartInstance = new Chart(canvas, cfg);
  }

  private renderAttribution(): void {
    const bt = this.store.current();
    const canvas = (this.attributionCanvas?.nativeElement
      ?? (document.querySelectorAll('canvas')[2] as HTMLCanvasElement | null));
    if (!canvas || !bt?.metrics) return;
    const attr = bt.metrics.per_agent_attribution || {};
    const labels = Object.keys(attr);
    if (labels.length === 0) return;
    this.attributionChartInstance?.destroy();
    const cfg: ChartConfiguration = {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'PnL delta if agent neutralized',
            data: labels.map((l) => attr[l]),
            backgroundColor: labels.map((l) => (attr[l] >= 0 ? '#16a34a' : '#dc2626')),
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { title: { display: true, text: 'USD' } } },
      },
    };
    this.attributionChartInstance = new Chart(canvas, cfg);
  }
}
