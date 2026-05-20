import {
  AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild, effect, inject,
} from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  Chart, ChartConfiguration, LineController, LineElement, PointElement,
  CategoryScale, LinearScale, Tooltip, Legend, Title, ScatterController,
  BarController, BarElement,
} from 'chart.js';
import { AppShellComponent } from '../shared/app-shell.component';
import { BacktestsStore } from '../../abstraction/backtests.store';
import { ENTRY_ANIMATION, baseLegend, personaColorById, readChartTheme } from '../shared/chart-defaults';

Chart.register(
  LineController, LineElement, PointElement, CategoryScale, LinearScale,
  Tooltip, Legend, Title, ScatterController, BarController, BarElement,
);

@Component({
  selector: 'hf-backtests-detail',
  standalone: true,
  imports: [CommonModule, DecimalPipe, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests', link:'/backtests'}, {label: store.current()?.name || ''}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Walk-forward backtest</div>
          <h1 style="margin-top:6px">{{ store.current()?.name || 'Backtest' }}</h1>
        </div>
        <div class="head-actions">
          @if (store.current(); as bt) {
            <span class="pill"><span class="dot"></span>Run cost · $ {{ (bt.total_cost_usd || 0) | number: '1.2-2' }}</span>
            <a class="btn" [routerLink]="['/backtests', bt.id, 'compare']">Compare…</a>
          }
          <a class="btn ghost" routerLink="/backtests">Back to list</a>
        </div>
      </div>

      @if (store.current(); as bt) {
        @if (bt.status !== 'done') {
          <section class="card" style="margin-bottom:18px;border-color:var(--acc-hold-soft);background:var(--acc-hold-soft)">
            <div class="card-bd">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <div style="font-size:13px">
                  <span style="font-weight:600">Status:</span> {{ bt.status }}
                  @if (bt.progress_message) {
                    <span style="color:var(--text-2)"> — {{ bt.progress_message }}</span>
                  }
                </div>
                <div class="mono" style="font-size:12px;color:var(--text-2)">{{ bt.progress_pct }}%</div>
              </div>
              <div class="cost-gauge" style="margin-top:8px;height:8px"><div class="fill" [style.width.%]="bt.progress_pct" style="background:var(--acc-hold)"></div></div>
              @if (bt.error_message) {
                <p style="color:var(--acc-short-fg);font-size:12px;margin:8px 0 0">{{ bt.error_message }}</p>
              }
            </div>
          </section>
        }

        @if (bt.metrics; as m) {
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px">
            <div class="kpi">
              <div class="k">Stitched OOS return</div>
              <div class="v">{{ m.total_return_pct | number: '1.2-2' }}%</div>
              <div class="d">Baseline: {{ m.baseline_return_pct | number: '1.2-2' }}%</div>
            </div>
            <div class="kpi">
              <div class="k">OOS Sharpe (mean)</div>
              <div class="v">{{ m.mean_oos_sharpe | number: '1.2-2' }}</div>
              <div class="d">IS mean: {{ m.mean_is_sharpe | number: '1.2-2' }} · σ {{ m.oos_sharpe_std | number: '1.2-2' }}</div>
            </div>
            <div class="kpi">
              <div class="k">Deflation (OOS / IS)</div>
              <div class="v"
                [style.color]="m.sharpe_deflation < 0.3 ? 'var(--acc-short-fg)' : m.sharpe_deflation < 0.5 ? 'var(--acc-hold-fg)' : 'var(--acc-long-fg)'">
                {{ m.sharpe_deflation | number: '1.2-2' }}
              </div>
              @if (m.sharpe_deflation < 0.3) {
                <div class="d" style="color:var(--acc-short-fg)">⚠ Strong overfitting suspected</div>
              }
            </div>
            <div class="kpi">
              <div class="k">Max drawdown</div>
              <div class="v">{{ m.max_drawdown_pct | number: '1.2-2' }}%</div>
              <div class="d">Turnover ann.: {{ m.turnover_pct | number: '1.0-0' }}%</div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px">
            <section class="card">
              <div class="card-hd"><span class="title">Stitched OOS equity vs baseline ({{ bt.baseline }})</span></div>
              <div class="card-bd"><div style="position:relative;height:260px"><canvas #equityChart></canvas></div></div>
            </section>
            <section class="card">
              <div class="card-hd"><span class="title">IS vs OOS Sharpe per fold</span></div>
              <div class="card-bd"><div style="position:relative;height:260px"><canvas #deflationChart></canvas></div></div>
            </section>
          </div>

          <section class="card" style="margin-bottom:18px">
            <div class="card-hd"><span class="title">Per-agent attribution (stitched OOS PnL delta)</span></div>
            <div class="card-bd"><div style="position:relative;height:240px"><canvas #attributionChart></canvas></div></div>
          </section>
        }

        <section class="card">
          <div class="card-hd"><span class="title">Folds</span></div>
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
                  <td class="mono" style="color:var(--text-2)">{{ f.is_start }} → {{ f.is_end }}</td>
                  <td class="mono" style="color:var(--text-2)">{{ f.oos_start }} → {{ f.oos_end }}</td>
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
