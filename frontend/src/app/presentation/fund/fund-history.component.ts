import { CommonModule, DecimalPipe } from '@angular/common';
import {
  AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild, effect, inject,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  CategoryScale, Chart, ChartConfiguration, Legend, LineController, LineElement,
  LinearScale, PointElement, Tooltip,
} from 'chart.js';
import { FundStore } from '../../abstraction/fund.store';
import { ENTRY_ANIMATION, baseLegend, readChartTheme } from '../shared/chart-defaults';

Chart.register(LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend);

// P10 §C2/§C4 — the LIVE fund equity history: time-weighted (flow-adjusted)
// return index vs SPY/QQQ, plus the per-pod realized-vs-validated strip.
// Answers "why not track the 3 Alpaca accounts?" with one chart.
@Component({
  selector: 'hf-fund-history',
  standalone: true,
  imports: [CommonModule, DecimalPipe, RouterLink],
  template: `
    @if (store.navHistory(); as h) {
      <section class="card hist">
        <div class="hist-head">
          <h2>Live equity history vs SPY/QQQ</h2>
          @if (h.available && h.aggregate.twr_pct !== null) {
            <span class="twr" [class.up]="h.aggregate.twr_pct >= 0" [class.down]="h.aggregate.twr_pct < 0">
              fund TWR {{ h.aggregate.twr_pct >= 0 ? '+' : '' }}{{ h.aggregate.twr_pct | number: '1.2-2' }}%
            </span>
          }
        </div>
        @if (h.available) {
          <p class="muted">
            Time-weighted (flow-adjusted) return index, base 100 — deposits, withdrawals and
            funding resets are excluded from returns, so transfers never print as performance.
            History accrues from the hourly guardrail sweep.
          </p>
          <div class="chart-wrap"><canvas #histChart role="img"
            aria-label="Fund time-weighted return index versus SPY and QQQ benchmarks. Per-pod figures are in the strip below."></canvas></div>
          <div class="pods">
            @for (a of h.per_account; track a.strategy_id) {
              <div class="pod">
                <a class="pod-name" [routerLink]="['/strategies', a.strategy_id, 'autopilot']">{{ a.name }}</a>
                <span class="pod-twr" [class.up]="(a.twr_pct ?? 0) >= 0" [class.down]="(a.twr_pct ?? 0) < 0">
                  {{ a.twr_pct !== null ? ((a.twr_pct >= 0 ? '+' : '') + (a.twr_pct | number: '1.2-2') + '%') : '—' }}
                </span>
                <span class="pod-exp" title="The validated annualized return from this pod's record-of-record backtest — the live number should converge toward it over time.">
                  validated:
                  @if (a.expected_ann_return_pct !== null) {
                    <a [routerLink]="['/backtests', a.expected_backtest_id]">{{ a.expected_ann_return_pct | number: '1.1-1' }}%/yr</a>
                  } @else { — }
                </span>
              </div>
            }
          </div>
        } @else {
          <p class="muted">{{ h.reason }}</p>
        }
      </section>
    }
  `,
  styles: [`
    .hist { padding: 16px; }
    .hist-head { display: flex; justify-content: space-between; align-items: baseline; }
    .hist-head h2 { margin: 0; }
    .twr { font-family: var(--font-mono); font-size: 13px; font-weight: 600; }
    .twr.up, .pod-twr.up { color: var(--acc-long-fg); }
    .twr.down, .pod-twr.down { color: var(--acc-short-fg); }
    .muted { color: var(--text-3); font-size: 12px; margin: 6px 0 10px; }
    .chart-wrap { position: relative; height: 240px; }
    .pods { display: flex; flex-wrap: wrap; gap: 18px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
    .pod { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; }
    .pod-name { font-weight: 600; text-decoration: none; color: var(--text); }
    .pod-name:hover { text-decoration: underline; }
    .pod-twr { font-family: var(--font-mono); }
    .pod-exp { color: var(--text-3); font-size: 11.5px; }
    .pod-exp a { color: var(--acc-info-fg); text-decoration: none; }
  `],
})
export class FundHistoryComponent implements OnInit, AfterViewInit, OnDestroy {
  readonly store = inject(FundStore);
  private chart: Chart | null = null;
  private viewReady = false;

  @ViewChild('histChart', { static: false }) canvas?: ElementRef<HTMLCanvasElement>;

  constructor() {
    effect(() => { this.store.navHistory(); if (this.viewReady) setTimeout(() => this.render(), 0); });
  }

  ngOnInit(): void {
    this.store.loadNavHistory().subscribe({ error: () => undefined });
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    setTimeout(() => this.render(), 0);
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
  }

  private render(): void {
    const h = this.store.navHistory();
    const canvas = this.canvas?.nativeElement;
    const pts = h?.aggregate?.points ?? [];
    if (!canvas || !h?.available || pts.length === 0) return;
    this.chart?.destroy();
    const t = readChartTheme();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: pts.map((p) => p.date),
        datasets: [
          {
            label: 'Fund (TWR index)',
            data: pts.map((p) => p.index),
            borderColor: t.info, backgroundColor: t.info + '14',
            tension: 0.1, pointRadius: 0, borderWidth: 1.6,
          },
          ...(pts.some((p) => p.spy !== undefined) ? [{
            label: 'SPY (TR)',
            data: pts.map((p) => p.spy ?? null),
            borderColor: t.long, borderDash: [4, 4],
            tension: 0.1, pointRadius: 0, borderWidth: 1,
          }] : []),
          ...(pts.some((p) => p.qqq !== undefined) ? [{
            label: 'QQQ (TR)',
            data: pts.map((p) => p.qqq ?? null),
            borderColor: t.short, borderDash: [4, 4],
            tension: 0.1, pointRadius: 0, borderWidth: 1,
          }] : []),
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
    this.chart = new Chart(canvas, cfg);
  }
}
