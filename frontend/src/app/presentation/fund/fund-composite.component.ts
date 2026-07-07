import { CommonModule, DecimalPipe } from '@angular/common';
import {
  AfterViewInit, Component, ElementRef, OnDestroy, OnInit, ViewChild, effect, inject,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  CategoryScale, Chart, ChartConfiguration, Legend, LineController, LineElement,
  LinearScale, LogarithmicScale, PointElement, Tooltip,
} from 'chart.js';
import { FundStore } from '../../abstraction/fund.store';
import { FundCompositeSeriesStats } from '../../core/models/autopilot.model';
import { ENTRY_ANIMATION, baseLegend, readChartTheme } from '../shared/chart-defaults';

Chart.register(
  LineController, LineElement, PointElement, CategoryScale, LinearScale,
  LogarithmicScale, Tooltip, Legend,
);

// P10 §B5 — "what do the 3 pods do TOGETHER?": the pods' stitched OOS
// validation curves combined at the live capital weights vs SPY-TR / QQQ-TR,
// with the honest two-sided framing (risk-adjusted alpha, raw-return gap).
@Component({
  selector: 'hf-fund-composite',
  standalone: true,
  imports: [CommonModule, DecimalPipe, RouterLink],
  template: `
    @if (store.composite(); as c) {
      @if (c.available) {
        <section class="card comp">
          <h2>Validated composite (backtest) vs SPY/QQQ — total return</h2>
          <p class="muted">
            The pods' walk-forward validation curves ({{ c.window?.start }} → {{ c.window?.end }},
            stitched out-of-sample only), each pod compounding independently at
            {{ weightsLabel() }} weights — the same structure as the live 3-account fund.
            This is the fund's <b>expected</b> shape from its records of record, not live performance.
          </p>
          <!-- P11 A3 — the review levers: re-lever the low-beta book toward market
               risk (BAB), and control for the bond-bull tailwind via the post-GFC
               sub-period. Both recompute from the same stored OOS curves. -->
          <div class="comp-controls">
            <label class="ctl">Leverage
              <select (change)="setLeverage($event)">
                @for (l of LEVERAGES; track l.v) {
                  <option [value]="l.v" [selected]="l.v === leverage">{{ l.label }}</option>
                }
              </select>
            </label>
            <div class="seg" role="group" aria-label="History window">
              <button type="button" [class.on]="subPeriod === 'full'"
                (click)="setSubPeriod('full')">Full history</button>
              <button type="button" [class.on]="subPeriod === 'post_gfc'"
                (click)="setSubPeriod('post_gfc')">Post-GFC (2010–)</button>
            </div>
            @if (leverage !== 1) {
              <span class="lev-note">
                Core ×{{ leverage | number: '1.1-1' }} — net of
                ~{{ (c.financing_bps ?? 200) / 100 | number: '1.0-1' }}%/yr financing (BAB)
              </span>
            }
          </div>
          <!-- P10 §G: the three numbers that define "alpha" here — shown even
               when unflattering. -->
          @if (headline(); as h) {
            <div class="alpha-strip">
              <div class="al">
                <span class="k">CAPM α vs SPY-TR</span>
                <span class="v" [class.up]="h.alpha >= 0" [class.down]="h.alpha < 0">
                  {{ h.alpha >= 0 ? '+' : '' }}{{ h.alpha | number: '1.1-1' }}%/yr
                </span>
              </div>
              <div class="al">
                <span class="k">Info ratio vs SPY-TR</span>
                <span class="v" [class.up]="h.ir >= 0" [class.down]="h.ir < 0">
                  {{ h.ir | number: '1.2-2' }}
                </span>
              </div>
              <div class="al">
                <span class="k">Raw-return gap vs SPY-TR</span>
                <span class="v" [class.up]="h.rawGap >= 0" [class.down]="h.rawGap < 0">
                  {{ h.rawGap >= 0 ? '+' : '' }}{{ h.rawGap | number: '1.1-1' }}%/yr
                </span>
              </div>
              <div class="al def">
                <span class="k">The goal</span>
                <span class="v small">absolute return: ≥ SPY-TR over a cycle at ≤ ½ its drawdown</span>
              </div>
            </div>
          }
          <div class="chart-wrap"><canvas #compChart role="img"
            aria-label="Fund composite equity curve versus SPY and QQQ total-return benchmarks over the validation window. The metrics table below carries the exact figures."></canvas></div>
          <table class="tbl mt-2">
            <thead><tr>
              <th>Series</th><th class="right">Ann. return</th><th class="right">Vol</th>
              <th class="right">Sharpe</th><th class="right">Max DD</th>
              <th class="right">Beta</th><th class="right">CAPM α /yr</th><th class="right">IR</th>
            </tr></thead>
            <tbody>
              @for (row of rows(); track row.label) {
                <tr [class.hero]="row.label === 'Fund composite'">
                  <td>{{ row.label }}</td>
                  <td class="num">{{ row.s.annualized_return_pct | number: '1.1-1' }}%</td>
                  <td class="num">{{ row.s.vol_annual_pct | number: '1.1-1' }}%</td>
                  <td class="num">{{ row.s.sharpe | number: '1.2-2' }}</td>
                  <td class="num">−{{ row.s.max_drawdown_pct | number: '1.1-1' }}%</td>
                  <td class="num">{{ row.s.vs_spy ? (row.s.vs_spy.beta | number: '1.2-2') : '—' }}</td>
                  <td class="num" [style.color]="(row.s.vs_spy?.alpha_annual_pct ?? 0) >= 0 ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                    {{ row.s.vs_spy ? ((row.s.vs_spy.alpha_annual_pct | number: '1.1-1') + '%') : '—' }}
                  </td>
                  <td class="num">{{ row.s.vs_spy ? (row.s.vs_spy.information_ratio | number: '1.2-2') : '—' }}</td>
                </tr>
              }
            </tbody>
          </table>
          <!-- P11 A3 — per-calendar-year returns: the edge is crisis alpha (2008 /
               2022 outperform) bought with a lag in every bull year. -->
          @if (c.calendar_years?.length) {
            <details class="cal">
              <summary>Calendar-year returns — where the edge lives (crisis alpha vs bull-year lag)</summary>
              <table class="tbl mt-2">
                <thead><tr>
                  <th>Year</th><th class="right">Composite{{ leverage !== 1 ? ' ×' + (leverage | number: '1.1-1') : '' }}</th>
                  <th class="right">SPY</th><th class="right">QQQ</th>
                </tr></thead>
                <tbody>
                  @for (y of c.calendar_years; track y.year) {
                    <tr>
                      <td>{{ y.year }}</td>
                      <td class="num" [style.color]="(y.composite ?? 0) >= 0 ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">{{ y.composite == null ? '—' : (y.composite | number: '1.1-1') + '%' }}</td>
                      <td class="num">{{ y.spy == null ? '—' : (y.spy | number: '1.1-1') + '%' }}</td>
                      <td class="num">{{ y.qqq == null ? '—' : (y.qqq | number: '1.1-1') + '%' }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </details>
          }
          <p class="muted small">
            Members:
            @for (m of c.members; track m.strategy_id; let last = $last) {
              <a [routerLink]="['/backtests', m.backtest_id]">{{ m.name }} ({{ m.weight * 100 | number: '1.0-0' }}%)</a>{{ last ? '' : ' · ' }}
            }
            — this fund is an <b>absolute-return, ~7–10%-vol, crisis-resilient book</b>,
            not a beta product. "Alpha" here = CAPM α / information ratio vs SPY-TR and
            QQQ-TR (return unexplained by market beta); a positive α alongside a
            raw-return gap means the edge is risk-adjusted (defensive), not raw
            outperformance. Full definition + kill criteria: ADR-0027.
          </p>
        </section>
      } @else if (c.reason !== 'no fund') {
        <section class="card comp">
          <h2>Validated composite (backtest)</h2>
          <p class="muted">Not available yet — {{ c.reason }}.
            @if (c.missing?.length) { <span>Missing: {{ c.missing!.join(', ') }}.</span> }
            Run a validation backtest from each pod card to populate this view.
          </p>
        </section>
      }
    }
  `,
  styles: [`
    .comp { padding: 16px; }
    .comp h2 { margin-top: 0; }
    .muted { color: var(--text-3); font-size: 12.5px; }
    .muted.small { font-size: 11.5px; margin-bottom: 0; }
    .muted a { color: var(--acc-info-fg); text-decoration: none; }
    .alpha-strip { display: flex; flex-wrap: wrap; gap: 22px; margin: 4px 0 10px; padding: 8px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
    .al { display: flex; flex-direction: column; gap: 2px; }
    .al .k { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-3); }
    .al .v { font-family: var(--font-mono); font-size: 14px; font-weight: 600; }
    .al .v.small { font-family: inherit; font-size: 11.5px; font-weight: 500; color: var(--text-2); }
    .al .v.up { color: var(--acc-long-fg); }
    .al .v.down { color: var(--acc-short-fg); }
    .chart-wrap { position: relative; height: 260px; }
    table.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    table.tbl th, table.tbl td { padding: 5px 10px; border-bottom: 1px solid var(--border); text-align: left; }
    table.tbl th.right, table.tbl td.num { text-align: right; font-variant-numeric: tabular-nums; }
    tr.hero td { font-weight: 600; }
    .mt-2 { margin-top: 8px; }
    .comp-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin: 4px 0 10px; }
    .ctl { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--text-3); }
    .ctl select { font-size: 12px; padding: 3px 6px; background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
    .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
    .seg button { font-size: 11.5px; padding: 4px 10px; background: var(--surface-2); color: var(--text-2); border: none; cursor: pointer; }
    .seg button.on { background: var(--acc-info-fg); color: #fff; }
    .lev-note { font-size: 11px; color: var(--text-3); }
    details.cal { margin-top: 10px; }
    details.cal summary { cursor: pointer; font-size: 12px; color: var(--acc-info-fg); }
  `],
})
export class FundCompositeComponent implements OnInit, AfterViewInit, OnDestroy {
  readonly store = inject(FundStore);
  private chart: Chart | null = null;
  private viewReady = false;

  // P11 A3 — review levers (re-fetch the composite from the same stored curves).
  readonly LEVERAGES = [
    { v: 1, label: '1.0× (unlevered)' },
    { v: 1.3, label: '1.3×' },
    { v: 1.5, label: '1.5× (research op. point)' },
    { v: 1.7, label: '1.7×' },
    { v: 2, label: '2.0×' },
  ];
  leverage = 1;
  subPeriod: 'full' | 'post_gfc' = 'full';

  @ViewChild('compChart', { static: false }) canvas?: ElementRef<HTMLCanvasElement>;

  constructor() {
    effect(() => { this.store.composite(); if (this.viewReady) setTimeout(() => this.render(), 0); });
  }

  ngOnInit(): void {
    this.reload();
  }

  private reload(): void {
    this.store
      .loadComposite({ leverage: this.leverage, subPeriod: this.subPeriod })
      .subscribe({ error: () => undefined });
  }

  setLeverage(e: Event): void {
    this.leverage = Number((e.target as HTMLSelectElement).value) || 1;
    this.reload();
  }

  setSubPeriod(p: 'full' | 'post_gfc'): void {
    if (this.subPeriod === p) return;
    this.subPeriod = p;
    this.reload();
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    setTimeout(() => this.render(), 0);
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
  }

  weightsLabel(): string {
    const members = this.store.composite()?.members ?? [];
    return members.map((m) => Math.round(m.weight * 100)).join('/');
  }

  // P10 §G: the three numbers that define "alpha" here — CAPM α, IR, and the
  // raw-return gap vs SPY-TR — shown even when unflattering.
  headline(): { alpha: number; ir: number; rawGap: number } | null {
    const metrics = this.store.composite()?.metrics;
    const comp = metrics?.['composite'];
    const spy = metrics?.['SPY'];
    if (!comp?.vs_spy || !spy) return null;
    return {
      alpha: comp.vs_spy.alpha_annual_pct,
      ir: comp.vs_spy.information_ratio,
      rawGap: comp.annualized_return_pct - spy.annualized_return_pct,
    };
  }

  rows(): { label: string; s: FundCompositeSeriesStats }[] {
    const metrics = this.store.composite()?.metrics ?? {};
    const out: { label: string; s: FundCompositeSeriesStats }[] = [];
    if (metrics['composite']) out.push({ label: 'Fund composite', s: metrics['composite'] });
    if (metrics['SPY']) out.push({ label: 'SPY buy & hold (TR)', s: metrics['SPY'] });
    if (metrics['QQQ']) out.push({ label: 'QQQ buy & hold (TR)', s: metrics['QQQ'] });
    return out;
  }

  private render(): void {
    const c = this.store.composite();
    const canvas = this.canvas?.nativeElement;
    if (!canvas || !c?.available || !c.points?.length) return;
    this.chart?.destroy();
    const t = readChartTheme();
    const pts = c.points;
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: pts.map((p) => p.date),
        datasets: [
          {
            label: 'Fund composite',
            data: pts.map((p) => p.composite),
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
          y: {
            type: 'logarithmic',
            grid: { color: t.grid },
            ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 10 } },
          },
        },
      },
    };
    this.chart = new Chart(canvas, cfg);
  }
}
