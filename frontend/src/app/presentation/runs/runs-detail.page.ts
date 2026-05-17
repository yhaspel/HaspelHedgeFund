import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { RunsStore } from '../../abstraction/runs.store';
import {
  AgentMessage,
  ALL_PERSONAS,
  PERSONA_IDS,
} from '../../core/models/run.model';

interface PersonaCard {
  id: string;
  displayName: string;
  version: string;
  signal: 'bullish' | 'neutral' | 'bearish' | 'unknown';
  confidence: number;
  thesis: string;
  keyRisks: string[];
  intrinsicValue: number | null;
  marginOfSafety: number | null;
}

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
              <span [class]="statusClass(run()!.status)">{{ run()!.status }}</span>
            </p>
          </div>
          <div>
            <p class="text-sm text-gray-500">As-of</p>
            <p class="text-lg font-medium">{{ run()!.as_of_date }}</p>
          </div>
          <div>
            <p class="text-sm text-gray-500">Personas</p>
            <p class="text-lg font-medium">{{ personas().length }}</p>
          </div>
          <div>
            <p class="text-sm text-gray-500">Total cost</p>
            <p class="text-lg font-medium">\${{ formatCost(run()!.total_cost_usd) }}</p>
          </div>
        </section>

        @if (run()!.error_message) {
          <section class="bg-red-50 border border-red-200 rounded p-4 mb-4">
            <p class="text-red-700 text-sm font-mono">{{ run()!.error_message }}</p>
          </section>
        }

        <!-- Portfolio Manager ticket -->
        @for (d of run()!.decisions; track d.id) {
          <section
            class="bg-white rounded shadow p-5 mb-4 border-l-4"
            [class.border-green-500]="d.action === 'buy'"
            [class.border-yellow-500]="d.action === 'hold'"
            [class.border-red-500]="d.action === 'sell'"
          >
            <div class="flex justify-between items-start">
              <div>
                <h2 class="text-xl font-semibold">
                  Order ticket: {{ d.action.toUpperCase() }} {{ d.ticker }}
                </h2>
                <p class="text-sm text-gray-500">
                  PM aggregate confidence {{ d.confidence }}
                  @if (d.risk_overrides.veto) {
                    · <span class="text-red-600 font-medium">RISK VETO</span>
                  }
                </p>
              </div>
              <div class="text-right text-sm">
                <p>Target qty: <span class="font-mono">{{ d.target_quantity }}</span></p>
                <p>Target weight: <span class="font-mono">{{ d.target_weight_pct }}%</span></p>
              </div>
            </div>
            <p class="text-sm whitespace-pre-wrap mt-3">{{ d.rationale }}</p>
          </section>
        }

        <!-- Risk Manager panel -->
        @if (riskOutput(); as risk) {
          <section class="bg-white rounded shadow p-5 mb-4">
            <h2 class="text-lg font-semibold mb-2">
              Risk Manager
              @if (risk['veto']) {
                <span class="ml-2 inline-block bg-red-100 text-red-700 text-xs px-2 py-0.5 rounded">VETO</span>
              }
            </h2>
            <div class="grid grid-cols-3 gap-4 text-sm">
              <div>
                <p class="text-gray-500">Max position</p>
                <p class="font-mono">{{ pct(risk['max_position_pct_for_this_trade']) }}</p>
              </div>
              <div>
                <p class="text-gray-500">Stop loss</p>
                <p class="font-mono">{{ pct(risk['stop_loss_pct']) }}</p>
              </div>
              <div>
                <p class="text-gray-500">Hard caps applied</p>
                <p class="font-mono text-xs">
                  {{ (asArray(risk['hard_caps_applied'])).join(', ') || 'none' }}
                </p>
              </div>
            </div>
            <p class="text-sm mt-3 whitespace-pre-wrap text-gray-700">
              {{ risk['rationale'] }}
            </p>
          </section>
        }

        <!-- Valuation -->
        @if (valuationOutput(); as v) {
          <section class="bg-white rounded shadow p-5 mb-4">
            <h2 class="text-lg font-semibold mb-2">Valuation</h2>
            <div class="grid grid-cols-4 gap-3 text-sm">
              <div><p class="text-gray-500">DCF</p><p class="font-mono">{{ num(v['dcf_fair_value']) }}</p></div>
              <div><p class="text-gray-500">Multiples</p><p class="font-mono">{{ num(v['multiples_fair_value']) }}</p></div>
              <div><p class="text-gray-500">Residual income</p><p class="font-mono">{{ num(v['residual_income_fair_value']) }}</p></div>
              <div><p class="text-gray-500">Current price</p><p class="font-mono">{{ num(v['current_price']) }}</p></div>
              <div><p class="text-gray-500">FV low</p><p class="font-mono">{{ num(v['fair_value_low']) }}</p></div>
              <div><p class="text-gray-500">FV high</p><p class="font-mono">{{ num(v['fair_value_high']) }}</p></div>
              <div class="col-span-2"><p class="text-gray-500">Upside</p><p class="font-mono">{{ num(v['upside_pct']) }}%</p></div>
            </div>
            <p class="text-xs text-gray-500 mt-2">
              Most sensitive: {{ v['most_sensitive_assumption'] }}
            </p>
          </section>
        }

        <!-- Persona council grid -->
        <section class="mb-4">
          <h2 class="text-lg font-semibold mb-3">Council</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            @for (p of personas(); track p.id) {
              <article class="bg-white rounded shadow p-4 border-t-4"
                [class.border-green-500]="p.signal === 'bullish'"
                [class.border-yellow-500]="p.signal === 'neutral'"
                [class.border-red-500]="p.signal === 'bearish'"
                [class.border-gray-300]="p.signal === 'unknown'"
              >
                <div class="flex justify-between items-baseline">
                  <h3 class="font-semibold">{{ p.displayName }}</h3>
                  <span class="text-xs font-mono text-gray-500">
                    {{ p.id }}&#64;{{ p.version }}
                  </span>
                </div>
                <p class="text-sm">
                  <span class="font-medium uppercase">{{ p.signal }}</span>
                  <span class="text-gray-500"> · {{ p.confidence }}% confidence</span>
                </p>
                <p class="text-sm mt-2">
                  {{ expanded().has(p.id) ? p.thesis : truncate(p.thesis, 200) }}
                </p>
                @if (p.thesis.length > 200) {
                  <button
                    type="button"
                    class="text-xs text-blue-600 hover:underline mt-1"
                    (click)="toggleExpand(p.id)"
                  >
                    {{ expanded().has(p.id) ? 'Collapse' : 'See full reasoning' }}
                  </button>
                }
                @if (p.keyRisks.length) {
                  <p class="text-xs text-gray-500 mt-2">
                    Risks: {{ p.keyRisks.join('; ') }}
                  </p>
                }
                @if (p.intrinsicValue !== null) {
                  <p class="text-xs text-gray-500 mt-1">
                    IV \${{ p.intrinsicValue }}, MoS {{ p.marginOfSafety }}%
                  </p>
                }
              </article>
            }
          </div>
        </section>

        <!-- Dissenting views -->
        @if (dissent().length) {
          <section class="bg-amber-50 border border-amber-200 rounded p-4 mb-4">
            <h2 class="text-lg font-semibold mb-2">Dissenting views</h2>
            <ul class="space-y-2 text-sm">
              @for (d of dissent(); track d.name) {
                <li>
                  <span class="font-medium">{{ displayName(d.name) }}</span>
                  ({{ d.signal }}, {{ d.confidence }}%):
                  <span class="text-gray-700">{{ d.thesis_summary }}</span>
                </li>
              }
            </ul>
          </section>
        }

        <!-- LLM calls -->
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
  readonly expanded = signal<Set<string>>(new Set());

  private readonly messageByAgent = computed(() => {
    const map = new Map<string, AgentMessage>();
    for (const m of this.run()?.messages ?? []) map.set(m.agent_name, m);
    return map;
  });

  readonly personas = computed<PersonaCard[]>(() => {
    const run = this.run();
    if (!run) return [];
    const versions = run.agent_versions ?? {};
    const msgs = this.messageByAgent();
    const out: PersonaCard[] = [];
    for (const p of ALL_PERSONAS) {
      if (!msgs.has(p.id)) continue;
      const payload = msgs.get(p.id)!.parsed_output as Record<string, unknown>;
      out.push({
        id: p.id,
        displayName: p.name,
        version: versions[p.id] ?? '—',
        signal: (payload['signal'] as PersonaCard['signal']) ?? 'unknown',
        confidence: Number(payload['confidence'] ?? 0),
        thesis: String(payload['thesis'] ?? ''),
        keyRisks: Array.isArray(payload['key_risks'])
          ? (payload['key_risks'] as string[])
          : [],
        intrinsicValue: payload['intrinsic_value_estimate'] as number | null,
        marginOfSafety: payload['margin_of_safety_pct'] as number | null,
      });
    }
    return out;
  });

  readonly riskOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('risk');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly valuationOutput = computed<Record<string, unknown> | null>(() => {
    const msg = this.messageByAgent().get('valuation');
    return msg ? (msg.parsed_output as Record<string, unknown>) : null;
  });

  readonly dissent = computed(() => {
    const run = this.run();
    if (!run) return [];
    const seen = new Set<string>();
    const out: { name: string; signal: string; confidence: number; thesis_summary: string }[] = [];
    for (const d of run.decisions) {
      for (const v of d.dissenting_views ?? []) {
        if (seen.has(v.name)) continue;
        seen.add(v.name);
        out.push(v);
      }
    }
    return out;
  });

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    if (id) this.store.pollRun(id);
  }

  ngOnDestroy(): void {
    this.store.stopPolling();
  }

  toggleExpand(id: string): void {
    const next = new Set(this.expanded());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.expanded.set(next);
  }

  truncate(s: string, n: number): string {
    return s.length <= n ? s : s.slice(0, n).trimEnd() + '…';
  }

  formatCost(s: string): string {
    const n = Number(s);
    return Number.isFinite(n) ? n.toFixed(4) : s;
  }

  num(v: unknown): string {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(2) : String(v);
  }

  pct(v: unknown): string {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    return Number.isFinite(n) ? (n * 100).toFixed(2) + '%' : String(v);
  }

  asArray(v: unknown): string[] {
    return Array.isArray(v) ? (v as string[]) : [];
  }

  statusClass(s: string): string {
    if (s === 'done') return 'text-green-600';
    if (s === 'failed') return 'text-red-600';
    return 'text-blue-600';
  }

  displayName(id: string): string {
    return ALL_PERSONAS.find((p) => p.id === id)?.name ?? id;
  }

  // Keep import alive for future filtering hooks.
  protected readonly _personaIds = PERSONA_IDS;
}
