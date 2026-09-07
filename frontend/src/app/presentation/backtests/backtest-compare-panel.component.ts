import {
  AfterViewInit,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  OnInit,
  Output,
  SimpleChanges,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  CategoryScale,
  Chart,
  ChartConfiguration,
  Legend,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from 'chart.js';

import { BacktestsStore } from '../../abstraction/backtests.store';
import {
  BacktestSummary,
  engineVersionBadge,
  engineVersionLabel,
} from '../../core/models/backtest.model';
import { apiErrorMessage } from '../../core/api/api-error';
import { readChartTheme } from '../shared/chart-defaults';

Chart.register(
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
);

interface CompareRow {
  key: string;
  label: string;
  a: number | null;
  b: number | null;
  delta: number | null;
  suffix: string;
  higherIsBetter: boolean;
  tone: 'good' | 'bad' | null;
}

/** null for absent / non-finite metrics — never a fabricated 0. */
function numeric(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

interface CompareSide {
  id: number;
  name: string;
  points: { date: string; portfolio_value: number }[];
  /** null while B is still running / failed — every metric then renders "—". */
  metrics: Record<string, unknown> | null;
}

interface ComparePayload {
  a: CompareSide;
  b: CompareSide;
}

/**
 * WAVE 3 item 9 — the backtest comparison, as a side-by-side panel INSIDE the
 * detail page instead of a bare full-screen route of its own.
 *
 * The old `/backtests/:id/compare` page rendered outside the app shell (no
 * nav, no breadcrumb, its own `min-h-screen` background), so comparing meant
 * leaving the backtest you were reading. This is the same UI as a drawer the
 * detail page opens and closes in place; the route still resolves and now
 * redirects here.
 *
 * Both bug fixes from the old page are preserved verbatim:
 *  1. per-metric `higherIsBetter` colouring — `max_drawdown_pct` and
 *     `turnover_pct` are POSITIVE magnitudes, so "bigger" is worse;
 *  2. missing metrics are `null` → "—" with no delta, never `Number(undefined
 *     ?? 0)` → a fabricated 0.00.
 */
@Component({
  selector: 'hf-backtest-compare-panel',
  standalone: true,
  imports: [FormsModule],
  template: `
    <section class="card cmp" data-test="compare-panel" role="region"
             aria-label="Compare this backtest against another">
      <div class="card-hd">
        <h2 class="title">Compare</h2>
        <div class="actions">
          <button type="button" class="btn sm" (click)="closed.emit()" data-test="compare-close">
            Close
          </button>
        </div>
      </div>

      <div class="card-bd">
        <div class="picker">
          <div class="side">
            <div class="side-k">A · this backtest</div>
            <div class="side-v">{{ data()?.a?.name || nameOf(backtestId) || ('#' + backtestId) }}</div>
          </div>
          <span class="vs">vs</span>
          <label class="side">
            <span class="side-k">B</span>
            <select
              [(ngModel)]="idB"
              (change)="load()"
              aria-label="Compare against backtest"
              class="input"
              data-test="compare-pick-b"
            >
              <option [ngValue]="null" disabled>Pick another backtest</option>
              <!-- Only DONE runs have metrics or a stitched curve; offering a
                   running/failed one produced an all-em-dash table. -->
              @for (b of comparable(); track b.id) {
                <option [ngValue]="b.id">{{ b.name }} ({{ engineBadge(b.engine_version) }})</option>
              }
            </select>
          </label>
        </div>

        @if (loadError(); as err) {
          <div class="err" role="alert" data-test="compare-error">
            <p>{{ err }}</p>
            <button type="button" class="btn sm" (click)="load()">Retry</button>
          </div>
        }

        @if (data(); as d) {
          @if (engineMismatch()) {
            <div class="mismatch" role="note" data-test="engine-mismatch">
              ⚠ These backtests were produced by different engine versions
              ({{ engineBadge(engineOf(backtestId)) }} vs {{ engineBadge(engineOf(idB)) }}).
              {{ engineLabel(engineOf(backtestId)) }} {{ engineLabel(engineOf(idB)) }}
              Read the differences below with that in mind.
            </div>
          }

          <h3 class="sub-h">Stitched OOS equity curves</h3>
          <div class="chart-wrap">
            <canvas
              #curve
              role="img"
              aria-label="Stitched out-of-sample equity curves for the two backtests being compared, overlaid over the same period."
            ></canvas>
          </div>

          <h3 class="sub-h">Metrics diff</h3>
          <div class="tbl-wrap">
            <table class="tbl" data-test="compare-metrics">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th class="right">A</th>
                  <th class="right">B</th>
                  <th class="right">B − A</th>
                </tr>
              </thead>
              <tbody>
                @for (m of metricsRows(d); track m.key) {
                  <tr>
                    <td>
                      {{ m.label }}
                      @if (!m.higherIsBetter) {
                        <span class="lower" title="Lower is better for this metric.">↓ better</span>
                      }
                    </td>
                    <td class="num">{{ fmt(m.a, m.suffix) }}</td>
                    <td class="num">{{ fmt(m.b, m.suffix) }}</td>
                    <td
                      class="num"
                      [style.color]="deltaColor(m.tone)"
                      [attr.data-test]="'compare-delta-' + m.key"
                    >
                      {{ fmtDelta(m.delta, m.suffix) }}
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else if (!loadError()) {
          <p class="muted">Pick a backtest B to compare against.</p>
        }
      </div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .card-bd {
        padding: 12px 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .picker {
        display: flex;
        align-items: flex-end;
        gap: 14px;
        flex-wrap: wrap;
      }
      .side {
        display: flex;
        flex-direction: column;
        gap: 3px;
      }
      .side-k {
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-3);
      }
      .side-v {
        font-size: var(--fs-13);
        font-weight: 500;
        color: var(--text);
      }
      .vs {
        color: var(--text-3);
        font-size: var(--fs-12);
        padding-bottom: 6px;
      }
      .picker select.input {
        min-width: 260px;
      }
      .err {
        border: 1px solid var(--acc-short);
        background: var(--acc-short-soft);
        border-radius: var(--r-6);
        padding: 8px 12px;
      }
      .err p {
        margin: 0 0 6px;
        font-size: var(--fs-12);
        color: var(--acc-short-fg);
      }
      .mismatch {
        border: 1px solid var(--acc-hold);
        background: var(--acc-hold-soft);
        border-radius: var(--r-6);
        padding: 8px 12px;
        font-size: var(--fs-12);
        color: var(--acc-hold-fg);
      }
      .sub-h {
        margin: 0;
        font-size: var(--fs-12);
        font-weight: 600;
        color: var(--text);
      }
      .chart-wrap {
        position: relative;
        height: 320px;
      }
      .tbl-wrap {
        overflow-x: auto;
      }
      .lower {
        margin-left: 6px;
        font-size: var(--fs-11);
        color: var(--text-3);
      }
      .muted {
        margin: 0;
        font-size: var(--fs-12);
        color: var(--text-3);
      }
    `,
  ],
})
export class BacktestComparePanelComponent
  implements OnInit, OnChanges, AfterViewInit, OnDestroy
{
  readonly store = inject(BacktestsStore);

  /** The backtest on the left (A) — the one whose detail page this is. */
  @Input({ required: true }) backtestId!: number;
  /** Pre-select B (from a `?compare=<id>` deep link). */
  @Input() initialCompareId: number | null = null;
  @Output() readonly closed = new EventEmitter<void>();

  readonly data = signal<ComparePayload | null>(null);
  readonly loadError = signal<string | null>(null);
  idB: number | null = null;

  private chart: Chart | null = null;
  private viewReady = false;

  readonly engineBadge = engineVersionBadge;
  readonly engineLabel = engineVersionLabel;

  @ViewChild('curve') canvas?: ElementRef<HTMLCanvasElement>;

  /** Only finished backtests can be compared — anything else has no metrics
   *  and no stitched curve. */
  comparable(): BacktestSummary[] {
    return this.store.list().filter((b) => b.id !== this.backtestId && b.status === 'done');
  }

  nameOf(id: number | null | undefined): string | null {
    if (!id) return null;
    return this.store.list().find((b) => b.id === id)?.name ?? null;
  }

  /** The engine that produced A's numbers (and B's), for the provenance line. */
  engineOf(id: number | null | undefined): number | null | undefined {
    if (!id) return undefined;
    return this.store.list().find((b) => b.id === id)?.engine_version;
  }

  /** True when A and B were produced by different engine versions — their
   *  numbers are not directly comparable. */
  engineMismatch(): boolean {
    const a = this.engineOf(this.backtestId);
    const b = this.engineOf(this.idB);
    return a !== undefined && b !== undefined && (a ?? null) !== (b ?? null);
  }

  ngOnInit(): void {
    this.store.listBacktests().subscribe({ error: () => undefined });
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['initialCompareId'] && this.initialCompareId) {
      this.idB = this.initialCompareId;
      this.load();
    }
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
  }

  load(): void {
    if (!this.idB) return;
    this.loadError.set(null);
    this.store.compare(this.backtestId, this.idB).subscribe({
      next: (d) => {
        this.data.set(d as unknown as ComparePayload);
        if (this.viewReady) {
          setTimeout(() => this.render(), 0);
          setTimeout(() => this.render(), 250);
        } else {
          setTimeout(() => this.render(), 250);
        }
      },
      error: (e: unknown) => {
        this.data.set(null);
        this.loadError.set(apiErrorMessage(e, 'Could not compare these backtests.'));
      },
    });
  }

  private render(): void {
    const d = this.data();
    const canvas = this.canvas?.nativeElement;
    if (!d || !canvas) return;
    // Align A and B by union of dates.
    const dates = new Set<string>();
    d.a.points.forEach((p) => dates.add(p.date));
    d.b.points.forEach((p) => dates.add(p.date));
    const sorted = Array.from(dates).sort();
    const mapA = new Map(d.a.points.map((p) => [p.date, p.portfolio_value]));
    const mapB = new Map(d.b.points.map((p) => [p.date, p.portfolio_value]));
    this.chart?.destroy();
    const theme = readChartTheme();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: sorted,
        datasets: [
          {
            label: `A — ${d.a.name}`,
            data: sorted.map((x) => mapA.get(x) ?? null),
            borderColor: theme.info,
            pointRadius: 0,
            tension: 0.1,
          },
          {
            label: `B — ${d.b.name}`,
            data: sorted.map((x) => mapB.get(x) ?? null),
            borderColor: theme.long,
            pointRadius: 0,
            tension: 0.1,
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
    this.chart = new Chart(canvas, cfg);
  }

  /**
   * One row per metric.
   *
   * Two bugs lived here (fixed earlier, preserved through the fold-in):
   *  1. every positive "B − A" was painted green. `max_drawdown_pct` and
   *     `turnover_pct` are POSITIVE magnitudes (apps/backtests/metrics.py:39-44
   *     "returned as a positive fraction"), so a deeper drawdown and a higher
   *     turnover — both worse — were shown as improvements. Each metric now
   *     declares `higherIsBetter`.
   *  2. a missing metric became `Number(undefined ?? 0)` → `0.00`, so a
   *     still-running backtest B read as "12.5pp worse" instead of "no result
   *     yet". Missing values are null and render as "—", with no delta.
   */
  metricsRows(d: ComparePayload): CompareRow[] {
    const rows: [string, string, string, boolean][] = [
      ['total_return_pct', 'Stitched OOS return', '%', true],
      ['mean_oos_sharpe', 'Mean OOS Sharpe', '', true],
      ['sharpe_deflation', 'Deflation (OOS / IS)', '', true],
      // A bigger drawdown magnitude is worse.
      ['max_drawdown_pct', 'Max drawdown', '%', false],
      // More turnover means more cost and more slippage.
      ['turnover_pct', 'Turnover (annualized)', '%', false],
      ['baseline_return_pct', 'Baseline return', '%', true],
    ];
    return rows.map(([key, label, suffix, higherIsBetter]) => {
      const a = numeric(d.a.metrics?.[key]);
      const b = numeric(d.b.metrics?.[key]);
      const delta = a === null || b === null ? null : b - a;
      let tone: 'good' | 'bad' | null = null;
      if (delta !== null && delta !== 0) {
        tone = delta > 0 === higherIsBetter ? 'good' : 'bad';
      }
      return { key, label, a, b, delta, suffix, higherIsBetter, tone };
    });
  }

  /** `var(--…)` colour for a delta cell, or null when there is nothing to say. */
  deltaColor(tone: 'good' | 'bad' | null): string | null {
    if (tone === 'good') return 'var(--acc-long-fg)';
    if (tone === 'bad') return 'var(--acc-short-fg)';
    return null;
  }

  /** "12.50%" or "—" when the backtest has no result for this metric. */
  fmt(v: number | null, suffix: string): string {
    return v === null ? '—' : `${v.toFixed(2)}${suffix}`;
  }

  fmtDelta(v: number | null, suffix: string): string {
    if (v === null) return '—';
    return `${v > 0 ? '+' : ''}${v.toFixed(2)}${suffix}`;
  }
}
