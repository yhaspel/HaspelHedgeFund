import { Component, OnInit, inject } from '@angular/core';
import { DecimalPipe, NgClass } from '@angular/common';
import { RouterLink } from '@angular/router';
import { BacktestsStore } from '../../abstraction/backtests.store';

@Component({
  selector: 'hf-backtests-list',
  standalone: true,
  imports: [RouterLink, DecimalPipe, NgClass],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-8">
        <h1 class="text-2xl font-semibold">Backtests</h1>
        <div class="flex gap-3">
          <a routerLink="/" class="text-blue-600 hover:underline self-center">Dashboard</a>
          <a
            routerLink="/backtests/new"
            class="bg-blue-600 text-white rounded px-4 py-2 hover:bg-blue-700"
          >New walk-forward</a>
        </div>
      </header>

      <div class="bg-white rounded shadow overflow-hidden">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-100 text-left">
            <tr>
              <th class="px-4 py-2">Name</th>
              <th class="px-4 py-2">Status</th>
              <th class="px-4 py-2">Period</th>
              <th class="px-4 py-2 text-right">Stitched OOS return</th>
              <th class="px-4 py-2 text-right">Mean OOS Sharpe</th>
              <th class="px-4 py-2 text-right">Deflation</th>
            </tr>
          </thead>
          <tbody>
            @for (bt of store.list(); track bt.id) {
              <tr class="border-t hover:bg-gray-50">
                <td class="px-4 py-2">
                  <a [routerLink]="['/backtests', bt.id]" class="text-blue-700 hover:underline">{{ bt.name }}</a>
                </td>
                <td class="px-4 py-2">
                  <span [ngClass]="{
                    'text-green-700': bt.status === 'done',
                    'text-yellow-700': bt.status === 'running' || bt.status === 'queued',
                    'text-red-700': bt.status === 'failed',
                    'text-gray-500': bt.status === 'cancelled'
                  }">{{ bt.status }}</span>
                  @if (bt.status === 'running' || bt.status === 'queued') {
                    <span class="text-xs text-gray-500"> · {{ bt.progress_pct }}%</span>
                  }
                </td>
                <td class="px-4 py-2 text-gray-700">{{ bt.start_date }} → {{ bt.end_date }}</td>
                <td class="px-4 py-2 text-right">
                  @if (bt.total_return_pct !== null) {
                    {{ bt.total_return_pct | number: '1.2-2' }}%
                  } @else { — }
                </td>
                <td class="px-4 py-2 text-right">
                  @if (bt.oos_sharpe !== null) { {{ bt.oos_sharpe | number: '1.2-2' }} } @else { — }
                </td>
                <td class="px-4 py-2 text-right">
                  @if (bt.deflation !== null) {
                    <span [ngClass]="{ 'text-red-600': bt.deflation < 0.3, 'text-yellow-700': bt.deflation >= 0.3 && bt.deflation < 0.5, 'text-green-700': bt.deflation >= 0.5 }">
                      {{ bt.deflation | number: '1.2-2' }}
                    </span>
                  } @else { — }
                </td>
              </tr>
            } @empty {
              <tr><td colspan="6" class="px-4 py-8 text-center text-gray-500">No backtests yet — start your first walk-forward.</td></tr>
            }
          </tbody>
        </table>
      </div>
    </div>
  `,
})
export class BacktestsListPage implements OnInit {
  readonly store = inject(BacktestsStore);
  ngOnInit(): void {
    this.store.listBacktests().subscribe();
  }
}
