import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { DecimalPipe, NgClass } from '@angular/common';
import { FormsModule } from '@angular/forms';
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
} from 'chart.js';
import { BacktestsStore } from '../../abstraction/backtests.store';

Chart.register(LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend);

interface ComparePayload {
  a: { id: number; name: string; points: { date: string; portfolio_value: number }[]; metrics: any };
  b: { id: number; name: string; points: { date: string; portfolio_value: number }[]; metrics: any };
}

@Component({
  selector: 'hf-backtests-compare',
  standalone: true,
  imports: [DecimalPipe, NgClass, FormsModule, RouterLink],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-semibold">Compare backtests</h1>
        <a routerLink="/backtests" class="text-blue-600 hover:underline">Back to list</a>
      </header>

      <div class="bg-white p-4 rounded shadow mb-6 flex items-end gap-4">
        <div class="text-sm">
          <div class="text-gray-500">A</div>
          <div class="font-medium">{{ data()?.a?.name || idA }}</div>
        </div>
        <span class="text-gray-400">vs</span>
        <label class="text-sm">
          <div class="text-gray-500">B</div>
          <select [(ngModel)]="idB" (change)="load()"
                  class="border rounded px-2 py-1 mt-1 min-w-[260px]">
            <option [ngValue]="null" disabled>Pick another backtest</option>
            @for (b of store.list(); track b.id) {
              @if (b.id !== idA) {
                <option [ngValue]="b.id">{{ b.name }} ({{ b.status }})</option>
              }
            }
          </select>
        </label>
      </div>

      @if (data(); as d) {
        <div class="bg-white p-4 rounded shadow mb-6">
          <h3 class="text-sm font-semibold mb-2">Stitched OOS equity curves</h3>
          <div style="position:relative; height:320px;"><canvas #curve></canvas></div>
        </div>

        <div class="bg-white p-4 rounded shadow">
          <h3 class="text-sm font-semibold mb-2">Metrics diff</h3>
          <table class="min-w-full text-sm">
            <thead class="bg-gray-100 text-left">
              <tr>
                <th class="px-3 py-2">Metric</th>
                <th class="px-3 py-2 text-right">A</th>
                <th class="px-3 py-2 text-right">B</th>
                <th class="px-3 py-2 text-right">B − A</th>
              </tr>
            </thead>
            <tbody>
              @for (m of metricsRows(d); track m.key) {
                <tr class="border-t">
                  <td class="px-3 py-2 text-gray-700">{{ m.label }}</td>
                  <td class="px-3 py-2 text-right">{{ m.a | number: '1.2-2' }}{{ m.suffix }}</td>
                  <td class="px-3 py-2 text-right">{{ m.b | number: '1.2-2' }}{{ m.suffix }}</td>
                  <td class="px-3 py-2 text-right"
                      [ngClass]="{ 'text-green-700': m.delta > 0, 'text-red-700': m.delta < 0 }">
                    {{ m.delta > 0 ? '+' : '' }}{{ m.delta | number: '1.2-2' }}{{ m.suffix }}
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else {
        <p class="text-sm text-gray-500">Pick a backtest B to compare against.</p>
      }
    </div>
  `,
})
export class BacktestsComparePage implements OnInit, AfterViewInit, OnDestroy {
  readonly store = inject(BacktestsStore);
  private readonly route = inject(ActivatedRoute);
  data = signal<ComparePayload | null>(null);
  idA = 0;
  idB: number | null = null;
  private chart: Chart | null = null;
  private viewReady = false;

  @ViewChild('curve') canvas?: ElementRef<HTMLCanvasElement>;

  ngOnInit(): void {
    this.idA = Number(this.route.snapshot.paramMap.get('id'));
    this.store.listBacktests().subscribe();
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
  }

  load(): void {
    if (!this.idB) return;
    this.store.compare(this.idA, this.idB).subscribe((d) => {
      this.data.set(d as any);
      setTimeout(() => this.render(), 0);
      setTimeout(() => this.render(), 250);
    });
  }

  private render(): void {
    const d = this.data();
    const canvas = this.canvas?.nativeElement ?? (document.querySelector('canvas') as HTMLCanvasElement | null);
    if (!d || !canvas) return;
    // Align A and B by union of dates.
    const dates = new Set<string>();
    d.a.points.forEach((p) => dates.add(p.date));
    d.b.points.forEach((p) => dates.add(p.date));
    const sorted = Array.from(dates).sort();
    const mapA = new Map(d.a.points.map((p) => [p.date, p.portfolio_value]));
    const mapB = new Map(d.b.points.map((p) => [p.date, p.portfolio_value]));
    this.chart?.destroy();
    const cfg: ChartConfiguration = {
      type: 'line',
      data: {
        labels: sorted,
        datasets: [
          { label: `A — ${d.a.name}`, data: sorted.map((x) => mapA.get(x) ?? null), borderColor: '#2563eb', pointRadius: 0, tension: 0.1 },
          { label: `B — ${d.b.name}`, data: sorted.map((x) => mapB.get(x) ?? null), borderColor: '#16a34a', pointRadius: 0, tension: 0.1 },
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

  metricsRows(d: ComparePayload): { key: string; label: string; a: number; b: number; delta: number; suffix: string }[] {
    const rows = [
      ['total_return_pct', 'Stitched OOS return', '%'],
      ['mean_oos_sharpe', 'Mean OOS Sharpe', ''],
      ['sharpe_deflation', 'Deflation (OOS / IS)', ''],
      ['max_drawdown_pct', 'Max drawdown', '%'],
      ['turnover_pct', 'Turnover (annualized)', '%'],
      ['baseline_return_pct', 'Baseline return', '%'],
    ] as const;
    return rows.map(([k, label, suffix]) => {
      const a = Number(d.a.metrics?.[k] ?? 0);
      const b = Number(d.b.metrics?.[k] ?? 0);
      return { key: k, label, a, b, delta: b - a, suffix };
    });
  }
}
