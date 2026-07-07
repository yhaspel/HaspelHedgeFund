import { CommonModule, DecimalPipe } from '@angular/common';
import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  effect,
  inject,
  signal,
} from '@angular/core';
import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  ChartConfiguration,
  Legend,
  LinearScale,
  Tooltip,
} from 'chart.js';

import { ApiClient } from '../../core/api/api-client';
import { AppShellComponent } from '../shared/app-shell.component';
import { ENTRY_ANIMATION, baseLegend, readChartTheme } from '../shared/chart-defaults';
import { SettingsTabsComponent } from './settings-tabs.component';

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend);

interface CostSummary {
  days: number;
  start: string;
  total_usd: string;
  by_day_model: { date: string; model: string; cost_usd: string }[];
  by_agent: { agent_name: string; cost_usd: string; calls: number }[];
  by_model: { model: string; cost_usd: string; calls: number }[];
}

const PALETTE = [
  '#5b8def', '#57c785', '#e6a23c', '#c96ff0',
  '#ef6f6f', '#3fb6c9', '#d98cae', '#8fa1b3',
];

/**
 * P5-SH WS2.4 — operator LLM-cost view. Daily instance-wide spend over the last
 * 30 days stacked by model, plus a by-agent breakdown. Data comes from
 * GET /api/costs/summary/ (superuser-only); a non-operator gets a friendly note.
 */
@Component({
  selector: 'hf-settings-costs-page',
  standalone: true,
  imports: [CommonModule, DecimalPipe, AppShellComponent, SettingsTabsComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Settings', link: '/settings/models' }, { label: 'Costs' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 class="mt-1.5">LLM cost</h1>
          <p class="sub">
            Daily LLM spend across this instance for the last
            {{ summary()?.days ?? 30 }} days, by model and by agent. Aggregated from
            recorded LLM calls; operator-only.
          </p>
        </div>
      </div>

      <hf-settings-tabs />

      <div role="tabpanel" aria-label="Cost settings">
        @if (error()) {
          <p class="alert" role="alert">{{ error() }}</p>
        }
        @if (summary(); as s) {
          <section class="card">
            <div class="card-hd">
              <h2 class="title">Daily spend by model</h2>
              <span class="total">total \${{ s.total_usd | number: '1.2-2' }}</span>
            </div>
            @if (s.by_day_model.length) {
              <div class="chart-wrap">
                <canvas #costChart role="img"
                  aria-label="Stacked daily LLM spend by model over the last 30 days"></canvas>
              </div>
            } @else {
              <p class="muted">No LLM spend recorded in this window.</p>
            }
          </section>

          <section class="card">
            <div class="card-hd"><h2 class="title">By agent</h2></div>
            <table class="tbl">
              <thead>
                <tr><th>Agent</th><th class="num">Calls</th><th class="num">Cost</th></tr>
              </thead>
              <tbody>
                @for (a of s.by_agent; track a.agent_name) {
                  <tr>
                    <td>{{ a.agent_name }}</td>
                    <td class="num">{{ a.calls }}</td>
                    <td class="num">\${{ a.cost_usd | number: '1.2-4' }}</td>
                  </tr>
                } @empty {
                  <tr><td colspan="3" class="muted">No spend recorded.</td></tr>
                }
              </tbody>
            </table>
          </section>
        } @else if (!error()) {
          <p class="muted">Loading…</p>
        }
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .total { font-family: var(--font-mono); font-size: 13px; color: var(--text-2); }
      .chart-wrap { position: relative; height: 280px; }
      .muted { color: var(--text-3); font-size: 12px; }
      .tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
      .tbl th, .tbl td {
        padding: 6px 8px;
        border-bottom: 1px solid var(--border);
        text-align: left;
      }
      .tbl .num { text-align: right; font-family: var(--font-mono); }
    `,
  ],
})
export class SettingsCostsPage implements OnInit, AfterViewInit, OnDestroy {
  private readonly api = inject(ApiClient);
  readonly summary = signal<CostSummary | null>(null);
  readonly error = signal<string>('');
  private chart: Chart | null = null;
  private viewReady = false;

  @ViewChild('costChart', { static: false }) canvas?: ElementRef<HTMLCanvasElement>;

  constructor() {
    // Re-render the chart whenever the data lands and the view is ready.
    effect(() => {
      this.summary();
      if (this.viewReady) setTimeout(() => this.render(), 0);
    });
  }

  ngOnInit(): void {
    this.api.get<CostSummary>('/costs/summary/?days=30').subscribe({
      next: (s) => this.summary.set(s),
      error: () =>
        this.error.set('Could not load cost data (operator access required).'),
    });
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    setTimeout(() => this.render(), 0);
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
  }

  private render(): void {
    const s = this.summary();
    const canvas = this.canvas?.nativeElement;
    if (!canvas || !s || s.by_day_model.length === 0) return;
    this.chart?.destroy();
    const t = readChartTheme();
    const dates = [...new Set(s.by_day_model.map((r) => r.date))].sort();
    const models = [...new Set(s.by_day_model.map((r) => r.model))];
    const byKey = new Map(
      s.by_day_model.map((r) => [`${r.date}|${r.model}`, Number(r.cost_usd)]),
    );
    const datasets = models.map((m, i) => ({
      label: m,
      data: dates.map((d) => byKey.get(`${d}|${m}`) ?? 0),
      backgroundColor: PALETTE[i % PALETTE.length],
      stack: 'spend',
      borderWidth: 0,
    }));
    const cfg: ChartConfiguration = {
      type: 'bar',
      data: { labels: dates, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: ENTRY_ANIMATION,
        plugins: { legend: baseLegend(t) },
        scales: {
          x: {
            stacked: true,
            grid: { color: t.grid },
            ticks: { color: t.axis, font: { family: 'JetBrains Mono', size: 9 } },
          },
          y: {
            stacked: true,
            grid: { color: t.grid },
            ticks: {
              color: t.axis,
              font: { family: 'JetBrains Mono', size: 10 },
              callback: (v) => '$' + v,
            },
          },
        },
      },
    };
    this.chart = new Chart(canvas, cfg);
  }
}
