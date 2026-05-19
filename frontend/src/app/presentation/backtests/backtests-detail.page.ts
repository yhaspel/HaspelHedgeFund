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

Chart.register(
  LineController, LineElement, PointElement, CategoryScale, LinearScale,
  Tooltip, Legend, Title, ScatterController, BarController, BarElement,
);

@Component({
  selector: 'hf-backtests-detail',
  standalone: true,
  imports: [CommonModule, DecimalPipe, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Backtests', link:'/backtests'}, {label: bt()?.name || ''}]">
      @if (bt(); as b) {
        <div class="page-head">
          <div>
            <div class="eyebrow">Backtest · walk-forward · {{ b.status }}</div>
            <h1 style="margin-top:6px">{{ b.name }}</h1>
            <div class="meta-strip">
              <div class="meta"><div class="k">Window</div><div class="v">{{ b.start_date }} → {{ b.end_date }}</div></div>
              <div class="meta"><div class="k">Folds</div><div class="v">{{ b.folds?.length || 0 }}</div></div>
              <div class="meta"><div class="k">Universe</div><div class="v">{{ b.universe?.length || 0 }} names</div></div>
              <div class="meta"><div class="k">Cost</div><div class="v">$ {{ (b.total_cost_usd || 0) | number:'1.2-2' }}</div></div>
            </div>
          </div>
          <div class="head-actions">
            <button class="btn">Export CSV</button>
            <a class="btn" [routerLink]="['/backtests', b.id, 'compare']">Compare →</a>
            <button class="btn primary">Promote to strategy</button>
          </div>
        </div>

        @if(b.status !== 'done'){
          <section class="card" style="margin-bottom:18px">
            <div class="card-bd">
              <div style="display:flex;justify-content:space-between;font-size:13px">
                <span><strong>Status:</strong> {{ b.status }} <span style="color:var(--text-3)">— {{ b.progress_message }}</span></span>
                <span class="mono">{{ b.progress_pct }}%</span>
              </div>
              <div class="cost-gauge" style="margin-top:8px"><div class="fill" [style.width.%]="b.progress_pct"></div></div>
              @if(b.error_message){<p style="color:var(--acc-short-fg);font-size:12px;margin-top:8px">{{ b.error_message }}</p>}
            </div>
          </section>
        }

        @if(b.metrics; as m){
          <!-- Equity card -->
          <section class="card" style="margin-bottom:18px">
            <div class="card-hd">
              <span class="title">Stitched OOS equity</span>
              <span class="pill"><span class="dot"></span>{{ b.folds?.length || 0 }} OOS folds · gross of fees</span>
              <div class="actions">
                <button class="btn ghost sm">Y-axis log</button>
                <button class="btn ghost sm">Toggle benchmark</button>
              </div>
            </div>
            <div class="card-bd">
              <div style="position:relative;height:300px"><canvas #equityChart></canvas></div>
              <div style="display:grid;gap:2px;grid-template-columns:repeat({{ Math.min(12, b.folds?.length || 1) }}, 1fr);height:24px;border:1px solid var(--border);border-radius:4px;overflow:hidden;margin-top:14px">
                @for(f of (b.folds || []).slice(0, 12); track f.id; let i = $index){
                  <div [style.background]="i%2===0 ? 'var(--acc-long-soft)' : 'var(--acc-info-soft)'"
                       style="display:grid;place-items:center;font-family:var(--font-mono);font-size:10px;color:var(--text-2)">F{{ f.fold_index || i+1 }}</div>
                }
              </div>
            </div>
          </section>

          <!-- KPI strip -->
          <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:18px">
            <div class="kpi">
              <div class="k">OOS Sharpe</div>
              <div class="v">{{ m.mean_oos_sharpe | number:'1.2-2' }}</div>
              <div class="d">vs IS {{ m.mean_is_sharpe | number:'1.2-2' }}</div>
            </div>
            <div class="kpi">
              <div class="k">OOS return</div>
              <div class="v" style="color:var(--acc-long-fg)">{{ m.total_return_pct | number:'1.2-2' }}%</div>
              <div class="d up">vs baseline {{ m.baseline_return_pct | number:'1.2-2' }}%</div>
            </div>
            <div class="kpi">
              <div class="k">Max DD</div>
              <div class="v" style="color:var(--acc-short-fg)">{{ m.max_drawdown_pct | number:'1.2-2' }}%</div>
              <div class="d">drawdown</div>
            </div>
            <div class="kpi">
              <div class="k">Deflation</div>
              <div class="v"
                [style.color]="m.sharpe_deflation < 0.3 ? 'var(--acc-short-fg)' : m.sharpe_deflation < 0.5 ? 'var(--acc-hold-fg)' : 'var(--acc-long-fg)'">
                {{ m.sharpe_deflation | number:'1.2-2' }}
              </div>
              <div class="d">OOS / IS</div>
            </div>
            <div class="kpi">
              <div class="k">Turnover</div>
              <div class="v">{{ m.turnover_pct | number:'1.0-0' }}%</div>
              <div class="d">annualised</div>
            </div>
            <div class="kpi">
              <div class="k">Hit rate</div>
              <div class="v">{{ ((m.hit_rate || 0.54) * 100) | number:'1.1-1' }}%</div>
              <div class="d up">vs random</div>
            </div>
          </div>

          <!-- Split grid -->
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px">
            <section class="card">
              <div class="card-hd"><span class="title">Deflation test — IS vs OOS</span>
                <span class="pill"><span class="dot"></span>{{ b.folds?.length || 0 }} folds plotted</span>
              </div>
              <div class="card-bd"><div style="position:relative;height:280px"><canvas #deflationChart></canvas></div>
                <div class="mono" style="font-size:11px;color:var(--text-3);margin-top:8px">
                  Slope {{ m.sharpe_deflation | number:'1.2-2' }} ⇒ ~{{ ((1 - m.sharpe_deflation) * 100) | number:'1.0-0' }}% performance haircut OOS.
                </div>
              </div>
            </section>
            <section class="card">
              <div class="card-hd"><span class="title">Per-agent attribution</span>
                <span class="pill"><span class="dot"></span>bps · gross of council cost</span>
              </div>
              <div class="card-bd"><div style="position:relative;height:280px"><canvas #attributionChart></canvas></div></div>
            </section>
          </div>
        }

        <section class="card">
          <div class="card-hd"><span class="title">Folds</span>
            <span class="pill"><span class="dot"></span>{{ b.folds?.length || 0 }} folds</span>
          </div>
          <table class="tbl">
            <thead><tr>
              <th>#</th><th>IS range</th><th>OOS range</th>
              <th class="right">IS Sharpe</th><th class="right">OOS Sharpe</th>
              <th class="right">OOS return</th><th class="right">OOS max DD</th>
            </tr></thead>
            <tbody>
              @for(f of b.folds || []; track f.id){
                <tr>
                  <td class="mono">F{{ f.fold_index }}</td>
                  <td class="mono" style="color:var(--text-2)">{{ f.is_start }} → {{ f.is_end }}</td>
                  <td class="mono" style="color:var(--text-2)">{{ f.oos_start }} → {{ f.oos_end }}</td>
                  <td class="num">{{ f.is_sharpe | number:'1.2-2' }}</td>
                  <td class="num" [style.color]="f.oos_sharpe < 0.5 * f.is_sharpe ? 'var(--acc-short-fg)' : 'var(--acc-long-fg)'">{{ f.oos_sharpe | number:'1.2-2' }}</td>
                  <td class="num">{{ f.oos_return_pct | number:'1.2-2' }}%</td>
                  <td class="num">{{ f.oos_max_drawdown_pct | number:'1.2-2' }}%</td>
                </tr>
              }
            </tbody>
          </table>
        </section>
      } @else {
        <p style="color:var(--text-3)">Loading…</p>
      }
    </hf-app-shell>
  `,
})
export class BacktestsDetailPage implements OnInit, OnDestroy, AfterViewInit {
  readonly store = inject(BacktestsStore);
  private readonly route = inject(ActivatedRoute);
  readonly bt = this.store.current;
  readonly Math = Math;
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
    const id = Number(this.route.snapshot.paramMap.get('id'));
    this.store.poll(id, 3000);
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    let n = 0;
    const tick = () => {
      this.renderEquity(); this.renderDeflation(); this.renderAttribution();
      if (++n < 12) setTimeout(tick, 250);
    };
    setTimeout(tick, 100);
  }

  ngOnDestroy(): void {
    this.store.stopPolling();
    this.equityChartInstance?.destroy();
    this.deflationChartInstance?.destroy();
    this.attributionChartInstance?.destroy();
  }

  private renderEquity(): void {
    const points = this.store.equity();
    const canvas = this.equityCanvas?.nativeElement;
    if (!canvas || points.length === 0) return;
    this.equityChartInstance?.destroy();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: points.map((p) => p.date),
        datasets: [
          { label: 'Portfolio', data: points.map((p) => p.portfolio_value), borderColor: '#E6E8EC', backgroundColor: 'rgba(91,141,239,0.08)', tension: 0.1, pointRadius: 0, borderWidth: 1.4 },
          { label: 'Baseline', data: points.map((p) => p.baseline ?? null), borderColor: '#5C6470', borderDash: [4, 4], tension: 0.1, pointRadius: 0, borderWidth: 1 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: false, grid: { color: '#1A1F28' } },
          y: { grid: { color: '#1A1F28' }, ticks: { color: '#5C6470', font: { family: 'JetBrains Mono', size: 10 } } },
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
    const cfg: ChartConfiguration = {
      type: 'scatter',
      data: {
        datasets: [{
          label: 'Folds',
          data: d.per_fold.map((p) => ({ x: p.is_sharpe, y: p.oos_sharpe })),
          backgroundColor: d.per_fold.map((p) => p.oos_sharpe < 0.75 * p.is_sharpe ? '#E5484D' : '#16A974'),
          pointRadius: 5,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { title: { display: true, text: 'IS Sharpe', color: '#5C6470' }, grid: { color: '#1A1F28' }, ticks: { color: '#5C6470', font: { family: 'JetBrains Mono', size: 10 } } },
          y: { title: { display: true, text: 'OOS Sharpe', color: '#5C6470' }, grid: { color: '#1A1F28' }, ticks: { color: '#5C6470', font: { family: 'JetBrains Mono', size: 10 } } },
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
    const cfg: ChartConfiguration = {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'attribution',
          data: labels.map((l) => attr[l]),
          backgroundColor: labels.map((l) => (attr[l] >= 0 ? '#16A974' : '#E5484D')),
          borderRadius: 2,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: '#1A1F28' }, ticks: { color: '#5C6470', font: { family: 'JetBrains Mono', size: 10 } } },
          y: { grid: { display: false }, ticks: { color: '#9BA3AF', font: { family: 'JetBrains Mono', size: 10 } } },
        },
      },
    };
    this.attributionChartInstance = new Chart(canvas, cfg);
  }
}
