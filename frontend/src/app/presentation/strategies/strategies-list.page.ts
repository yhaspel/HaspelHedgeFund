import { Component, OnInit, inject } from '@angular/core';
import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { StrategiesStore } from '../../abstraction/strategies.store';

@Component({
  selector: 'hf-strategies-list',
  standalone: true,
  imports: [RouterLink, DatePipe],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-semibold">Strategies</h1>
        <div class="flex gap-2">
          <a routerLink="/strategies/new"
             class="bg-emerald-600 text-white rounded px-4 py-2 text-sm">
            + New strategy
          </a>
          <a routerLink="/" class="text-blue-600 text-sm self-center hover:underline">
            Dashboard
          </a>
        </div>
      </header>

      <section class="bg-white rounded shadow">
        @if (store.strategies().length === 0) {
          <p class="p-6 text-gray-500 text-sm">
            No strategies yet. Create one to start the autonomous long-short engine.
          </p>
        } @else {
          <table class="w-full text-sm">
            <thead class="text-left text-xs text-gray-500 border-b">
              <tr>
                <th class="px-4 py-2">Name</th>
                <th class="px-4 py-2">Universe</th>
                <th class="px-4 py-2">Gross / Net</th>
                <th class="px-4 py-2">K longs/shorts</th>
                <th class="px-4 py-2">Preset</th>
                <th class="px-4 py-2">Last run</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (s of store.strategies(); track s.id) {
                <tr class="border-t">
                  <td class="px-4 py-2 font-medium">{{ s.name }}</td>
                  <td class="px-4 py-2">{{ s.universe_name }}</td>
                  <td class="px-4 py-2 font-mono">
                    {{ s.target_gross_pct }} / {{ s.target_net_pct }}
                  </td>
                  <td class="px-4 py-2 font-mono">
                    {{ s.top_k_longs }} / {{ s.top_k_shorts }}
                  </td>
                  <td class="px-4 py-2">{{ s.model_preset }}</td>
                  <td class="px-4 py-2 text-xs text-gray-500">
                    {{ s.last_run_at ? (s.last_run_at | date: 'short') : '—' }}
                  </td>
                  <td class="px-4 py-2 text-right">
                    <a [routerLink]="['/strategies', s.id]"
                       class="text-blue-600 text-sm hover:underline">Open</a>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    </div>
  `,
})
export class StrategiesListPage implements OnInit {
  readonly store = inject(StrategiesStore);
  ngOnInit(): void {
    this.store.list().subscribe();
  }
}
