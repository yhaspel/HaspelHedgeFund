import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { RunsStore } from '../../abstraction/runs.store';

@Component({
  selector: 'hf-runs-detail',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="min-h-screen bg-gray-50 p-8">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-semibold">
          Run #{{ run()?.id }} — {{ run()?.tickers?.join(', ') }}
        </h1>
        <a routerLink="/runs/new" class="text-blue-600 hover:underline">New run</a>
      </header>

      @if (!run()) {
        <p class="text-gray-500">Loading…</p>
      } @else {
        <section class="bg-white rounded shadow p-4 mb-4 flex justify-between">
          <div>
            <p class="text-sm text-gray-500">Status</p>
            <p class="text-lg font-medium">
              <span [class]="statusClass(run()!.status)">
                {{ run()!.status }}
              </span>
            </p>
          </div>
          <div>
            <p class="text-sm text-gray-500">As-of</p>
            <p class="text-lg font-medium">{{ run()!.as_of_date }}</p>
          </div>
          <div>
            <p class="text-sm text-gray-500">Total cost</p>
            <p class="text-lg font-medium">
              \${{ formatCost(run()!.total_cost_usd) }}
            </p>
          </div>
        </section>

        @if (run()!.error_message) {
          <section class="bg-red-50 border border-red-200 rounded p-4 mb-4">
            <p class="text-red-700 text-sm font-mono">{{ run()!.error_message }}</p>
          </section>
        }

        @for (msg of run()!.messages; track msg.id) {
          <section class="bg-white rounded shadow p-4 mb-4">
            <h2 class="font-semibold capitalize mb-2">{{ msg.agent_name }}</h2>
            <pre class="text-xs bg-gray-50 p-3 rounded overflow-x-auto">{{ pretty(msg.parsed_output) }}</pre>
          </section>
        }

        @for (d of run()!.decisions; track d.id) {
          <section class="bg-white rounded shadow p-4 mb-4 border-l-4"
                   [class.border-green-500]="d.action === 'buy'"
                   [class.border-yellow-500]="d.action === 'hold'"
                   [class.border-red-500]="d.action === 'sell'">
            <h2 class="font-semibold mb-2">
              Decision: {{ d.action.toUpperCase() }} {{ d.ticker }}
              <span class="text-sm text-gray-500">(confidence {{ d.confidence }})</span>
            </h2>
            <p class="text-sm whitespace-pre-wrap">{{ d.rationale }}</p>
          </section>
        }

        @if (run()!.llm_calls.length) {
          <section class="bg-white rounded shadow p-4">
            <h2 class="font-semibold mb-2">LLM calls</h2>
            <table class="w-full text-sm">
              <thead class="text-left text-gray-500">
                <tr>
                  <th>Agent</th><th>Provider</th><th>Model</th>
                  <th class="text-right">In</th><th class="text-right">Out</th>
                  <th class="text-right">Cost</th><th class="text-right">ms</th>
                </tr>
              </thead>
              <tbody>
                @for (c of run()!.llm_calls; track c.id) {
                  <tr class="border-t">
                    <td>{{ c.agent_name }}</td>
                    <td>{{ c.provider }}</td>
                    <td class="font-mono text-xs">{{ c.model }}</td>
                    <td class="text-right">{{ c.prompt_tokens }}</td>
                    <td class="text-right">{{ c.completion_tokens }}</td>
                    <td class="text-right">\${{ formatCost(c.cost_usd) }}</td>
                    <td class="text-right">{{ c.latency_ms }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        }
      }
    </div>
  `,
})
export class RunsDetailPage implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  readonly store = inject(RunsStore);
  readonly run = this.store.currentRun;

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    if (id) this.store.pollRun(id);
  }

  ngOnDestroy(): void {
    this.store.stopPolling();
  }

  pretty(obj: unknown): string {
    return JSON.stringify(obj, null, 2);
  }

  formatCost(s: string): string {
    const n = Number(s);
    return Number.isFinite(n) ? n.toFixed(4) : s;
  }

  statusClass(s: string): string {
    if (s === 'done') return 'text-green-600';
    if (s === 'failed') return 'text-red-600';
    return 'text-blue-600';
  }
}
