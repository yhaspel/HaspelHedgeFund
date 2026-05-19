import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { CycleDetail } from '../../core/models/strategy.model';

@Component({
  selector: 'hf-strategies-detail',
  standalone: true,
  imports: [RouterLink, DatePipe, DecimalPipe],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-semibold">
            {{ store.currentStrategy()?.name ?? 'Strategy' }}
          </h1>
          <p class="text-sm text-gray-500">
            Universe {{ store.currentStrategy()?.universe_name }} ·
            Gross {{ store.currentStrategy()?.target_gross_pct }} ·
            Net {{ store.currentStrategy()?.target_net_pct }} ·
            K {{ store.currentStrategy()?.top_k_longs }}L /
              {{ store.currentStrategy()?.top_k_shorts }}S
          </p>
        </div>
        <div class="flex gap-2">
          <button (click)="runNow()" [disabled]="running()"
                  class="bg-emerald-600 text-white rounded px-4 py-2 text-sm disabled:opacity-50"
                  data-test="run-now">
            {{ running() ? 'Dispatching…' : 'Run cycle now' }}
          </button>
          <a routerLink="/strategies" class="text-blue-600 self-center text-sm hover:underline">
            All strategies
          </a>
        </div>
      </header>

      @if (notice()) {
        <p class="bg-blue-50 border border-blue-200 text-blue-800 text-sm rounded p-3 mb-4">
          {{ notice() }}
        </p>
      }

      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section class="bg-white rounded shadow p-5 lg:col-span-1">
          <h2 class="font-medium mb-2">Cycles</h2>
          @if (store.cycles().length === 0) {
            <p class="text-xs text-gray-500">No cycles yet — click "Run cycle now".</p>
          } @else {
            <ul class="space-y-1 text-sm">
              @for (c of store.cycles(); track c.id) {
                <li>
                  <button class="text-blue-600 hover:underline"
                          (click)="openCycle(c.id)">
                    {{ c.as_of_date }} · {{ c.status }} · g {{ c.gross_pct }}
                  </button>
                </li>
              }
            </ul>
          }
        </section>

        @if (cycle()) {
          <section class="bg-white rounded shadow p-5 lg:col-span-2 space-y-5">
            <div class="flex justify-between text-sm">
              <div>
                <h2 class="font-medium">Cycle {{ cycle()!.as_of_date }}</h2>
                <p class="text-xs text-gray-500">
                  Status {{ cycle()!.status }} ·
                  {{ cycle()!.finished_at ? (cycle()!.finished_at | date: 'short') : '—' }}
                </p>
              </div>
              <div class="text-right text-xs">
                <div>Gross {{ cycle()!.gross_pct | number: '1.4-4' }}</div>
                <div>Net {{ cycle()!.net_pct | number: '1.4-4' }}</div>
              </div>
            </div>

            @if (cycle()!.error_message) {
              <p class="text-red-600 text-sm">{{ cycle()!.error_message }}</p>
            }

            <div class="grid grid-cols-2 gap-4">
              <div>
                <h3 class="text-xs font-semibold uppercase text-emerald-700">Long book</h3>
                @if (longs().length === 0) {
                  <p class="text-xs text-gray-500">No longs.</p>
                } @else {
                  <table class="w-full text-xs font-mono">
                    @for (row of longs(); track row.ticker) {
                      <tr>
                        <td>{{ row.ticker }}</td>
                        <td class="text-right">{{ row.weight | number: '1.2-2' }}%</td>
                      </tr>
                    }
                  </table>
                }
              </div>
              <div>
                <h3 class="text-xs font-semibold uppercase text-red-700">Short book</h3>
                @if (shorts().length === 0) {
                  <p class="text-xs text-gray-500">No shorts.</p>
                } @else {
                  <table class="w-full text-xs font-mono">
                    @for (row of shorts(); track row.ticker) {
                      <tr>
                        <td>{{ row.ticker }}</td>
                        <td class="text-right">{{ row.weight | number: '1.2-2' }}%</td>
                      </tr>
                    }
                  </table>
                }
              </div>
            </div>

            <div>
              <h3 class="text-xs font-semibold uppercase text-gray-600 mb-1">
                Sector exposure (signed)
              </h3>
              <table class="w-full text-xs font-mono">
                @for (row of sectorRows(); track row.sector) {
                  <tr>
                    <td>{{ row.sector || '—' }}</td>
                    <td class="text-right"
                        [class.text-emerald-700]="row.weight > 0"
                        [class.text-red-700]="row.weight < 0">
                      {{ row.weight | number: '1.2-2' }}%
                    </td>
                  </tr>
                }
              </table>
            </div>

            <div>
              <h3 class="text-xs font-semibold uppercase text-gray-600 mb-1">
                Rebalance orders ({{ cycle()!.orders.length }})
              </h3>
              @if (cycle()!.orders.length === 0) {
                <p class="text-xs text-gray-500">No orders this cycle.</p>
              } @else {
                <table class="w-full text-xs">
                  <thead class="text-gray-500 text-left">
                    <tr><th>Seq</th><th>Side</th><th>Ticker</th>
                      <th class="text-right">Qty</th>
                      <th class="text-right">Limit</th>
                      <th>Reason</th>
                      <th class="text-right">Notional</th></tr>
                  </thead>
                  <tbody>
                    @for (o of cycle()!.orders; track o.id) {
                      <tr class="border-t font-mono">
                        <td>{{ o.sequence }}</td>
                        <td [class.text-emerald-700]="o.side === 'buy' || o.side === 'cover'"
                            [class.text-red-700]="o.side === 'sell' || o.side === 'short'">
                          {{ o.side }}
                        </td>
                        <td>{{ o.ticker }}</td>
                        <td class="text-right">{{ o.quantity }}</td>
                        <td class="text-right">{{ o.limit_price ?? 'mkt' }}</td>
                        <td>{{ o.reason }}</td>
                        <td class="text-right">\${{ o.estimated_notional_usd }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              }
            </div>

            @if (cycle()!.rejected_candidates.length > 0) {
              <details>
                <summary class="text-xs text-gray-500 cursor-pointer">
                  Rejected candidates ({{ cycle()!.rejected_candidates.length }})
                </summary>
                <ul class="text-xs font-mono mt-2 space-y-1">
                  @for (r of cycle()!.rejected_candidates; track $index) {
                    <li>
                      {{ r.ticker ?? '(sector)' }} — {{ r.reason }}
                    </li>
                  }
                </ul>
              </details>
            }
          </section>
        }
      </div>
    </div>
  `,
})
export class StrategiesDetailPage implements OnInit, OnDestroy {
  readonly store = inject(StrategiesStore);
  private readonly route = inject(ActivatedRoute);
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  running = signal(false);
  notice = signal<string | null>(null);
  cycle = signal<CycleDetail | null>(null);

  strategyId = 0;

  longs() {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.target_weights)
      .filter(([, w]) => Number(w) > 0)
      .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
      .sort((a, b) => b.weight - a.weight);
  }
  shorts() {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.target_weights)
      .filter(([, w]) => Number(w) < 0)
      .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
      .sort((a, b) => a.weight - b.weight);
  }
  sectorRows() {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.sector_exposure)
      .map(([sector, w]) => ({ sector, weight: Number(w) * 100 }))
      .sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  }

  ngOnInit(): void {
    this.strategyId = Number(this.route.snapshot.paramMap.get('id'));
    this.store.detail(this.strategyId).subscribe();
    this.refreshCycles();
  }
  ngOnDestroy(): void {
    if (this.pollHandle) clearInterval(this.pollHandle);
  }

  refreshCycles(): void {
    this.store.listCycles(this.strategyId).subscribe((cs) => {
      if (cs.length) {
        const c = this.cycle();
        if (!c) this.openCycle(cs[0].id);
        else this.openCycle(c.id);
      }
    });
  }

  openCycle(id: number): void {
    this.store.cycleDetail(this.strategyId, id).subscribe((d) => {
      this.cycle.set(d);
    });
  }

  runNow(): void {
    this.running.set(true);
    this.notice.set(null);
    this.store.runNow(this.strategyId).subscribe({
      next: (r) => {
        this.running.set(false);
        this.notice.set(`Cycle dispatched (task ${r.task_id}). Refreshing every 5s.`);
        if (this.pollHandle) clearInterval(this.pollHandle);
        this.pollHandle = setInterval(() => this.refreshCycles(), 5000);
      },
      error: () => {
        this.running.set(false);
        this.notice.set('Failed to dispatch cycle.');
      },
    });
  }
}
