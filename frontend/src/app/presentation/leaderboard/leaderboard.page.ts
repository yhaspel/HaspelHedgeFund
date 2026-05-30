import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import { ApiClient } from '../../core/api/api-client';
import { AppShellComponent } from '../shared/app-shell.component';

interface AgentRow {
  agent_name: string;
  model_id: string;
  n_decisions: number;
  n_directional: number;
  hit_rate: string | null;
  hit_rate_ci_low: string | null;
  hit_rate_ci_high: string | null;
  brier_score: string | null;
  avg_forward_return_bps: string | null;
  pnl_contribution_bps: string | null;
  provisional: boolean;
}
interface ModelRow {
  model_id: string;
  agent_role: string;
  n_decisions: number;
  avg_cost_per_decision_usd: string | null;
  cost_adjusted_return_bps: string | null;
  provisional: boolean;
}
interface StrategyRow {
  strategy: number | null;
  strategy_name: string | null;
  flavor: string;
  flavor_display: string;
  n_cycles: number;
  sharpe: string | null;
  sortino: string | null;
  max_drawdown_pct: string | null;
  hit_rate: string | null;
  annualised_turnover_pct: string | null;
  avg_cost_per_cycle_usd: string | null;
  council_alpha_bps: string | null;
  council_cost_usd: string | null;
  council_net_value_usd: string | null;
  baseline_version: string;
  provisional: boolean;
}
interface DecisionRow {
  run_id: number;
  ticker: string;
  as_of_date: string;
  signal: string | null;
  confidence: number | null;
  forward_return_pct: number | null;
}

@Component({
  selector: 'hf-leaderboard-page',
  standalone: true,
  imports: [CommonModule, AppShellComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Leaderboard' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Performance</div>
          <h1 class="mt-1.5">Leaderboard</h1>
          <p class="sub">
            Which agents and strategies have actually produced good calls over
            the rolling forward-test window.
          </p>
        </div>
        <div class="head-right">
          <select
            class="sel"
            [value]="window()"
            (change)="onWindow($any($event.target).value)"
            aria-label="Window"
          >
            <option value="30d">30 days</option>
            <option value="90d">90 days</option>
            <option value="lifetime">Lifetime</option>
          </select>
          <button class="btn sm" (click)="recompute()" [disabled]="busy()">
            {{ busy() ? 'Recomputing…' : 'Recompute' }}
          </button>
        </div>
      </div>

      <div class="tabs" role="tablist">
        <button
          class="tab"
          [class.active]="tab() === 'agents'"
          role="tab"
          [attr.aria-selected]="tab() === 'agents'"
          (click)="tab.set('agents')"
        >
          Agents
        </button>
        <button
          class="tab"
          [class.active]="tab() === 'strategies'"
          role="tab"
          [attr.aria-selected]="tab() === 'strategies'"
          (click)="tab.set('strategies')"
        >
          Strategies
        </button>
      </div>

      @if (tab() === 'agents') {
        <section class="card">
          <div class="card-hd"><h2 class="title">Top personas</h2></div>
          @if (agents().length === 0) {
            <p class="empty">
              No scored decisions yet for this window. The leaderboard fills in
              once runs have a forward-return window (≈5 trading days) behind
              them. Try “Recompute”.
            </p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Persona</th>
                    <th>Model</th>
                    <th class="r">n</th>
                    <th class="r">Hit rate</th>
                    <th class="r">Brier</th>
                    <th class="r">Avg fwd (bps)</th>
                    <th class="r">PnL (bps)</th>
                  </tr>
                </thead>
                <tbody>
                  @for (a of agents(); track a.agent_name + a.model_id) {
                    <tr class="clk" (click)="drill(a.agent_name)">
                      <td>
                        {{ a.agent_name }}
                        @if (a.provisional) {
                          <span class="badge" title="Low sample — treat as provisional">prov.</span>
                        }
                      </td>
                      <td class="muted">{{ a.model_id || '—' }}</td>
                      <td class="r">{{ a.n_directional }}</td>
                      <td class="r">{{ pct(a.hit_rate) }}</td>
                      <td class="r">{{ num(a.brier_score) }}</td>
                      <td class="r">{{ num(a.avg_forward_return_bps) }}</td>
                      <td class="r">{{ a.pnl_contribution_bps ?? '—' }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>

        <section class="card">
          <div class="card-hd"><h2 class="title">Top models per role</h2></div>
          @if (models().length === 0) {
            <p class="empty">No model data for this window.</p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Role</th>
                    <th class="r">n</th>
                    <th class="r">$/decision</th>
                    <th class="r">Cost-adj (bps/$)</th>
                  </tr>
                </thead>
                <tbody>
                  @for (m of models(); track m.model_id + m.agent_role) {
                    <tr>
                      <td>{{ m.model_id }}</td>
                      <td class="muted">{{ m.agent_role }}</td>
                      <td class="r">{{ m.n_decisions }}</td>
                      <td class="r">{{ usd(m.avg_cost_per_decision_usd) }}</td>
                      <td class="r">{{ num(m.cost_adjusted_return_bps) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>

        @if (drillAgent()) {
          <section class="card">
            <div class="card-hd">
              <h2 class="title">Decisions — {{ drillAgent() }}</h2>
              <button class="btn sm ghost" (click)="drillAgent.set(null)">Close</button>
            </div>
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Ticker</th>
                    <th>As of</th>
                    <th>Signal</th>
                    <th class="r">Conf</th>
                    <th class="r">Fwd 5d</th>
                  </tr>
                </thead>
                <tbody>
                  @for (d of decisions(); track d.run_id) {
                    <tr class="clk" (click)="openRun(d.run_id)">
                      <td>#{{ d.run_id }}</td>
                      <td>{{ d.ticker }}</td>
                      <td class="muted">{{ d.as_of_date }}</td>
                      <td>{{ d.signal }}</td>
                      <td class="r">{{ d.confidence ?? '—' }}</td>
                      <td class="r">
                        {{ d.forward_return_pct !== null ? d.forward_return_pct + '%' : '—' }}
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </section>
        }
      } @else {
        <section class="card">
          <div class="card-hd">
            <h2 class="title">My strategies</h2>
            <select
              class="sel sm"
              [value]="stratSort()"
              (change)="onStratSort($any($event.target).value)"
              aria-label="Sort strategies"
            >
              <option value="sharpe">Sort: Sharpe</option>
              <option value="council_alpha_bps">Sort: Council α</option>
              <option value="total_return_pct">Sort: Return</option>
            </select>
          </div>
          @if (strategies().length === 0) {
            <p class="empty">
              No strategy cycles scored yet for this window. Rows appear once a
              strategy has ≥ 2 completed cycles with marks.
            </p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Flavor</th>
                    <th class="r">Cycles</th>
                    <th class="r">Sharpe</th>
                    <th class="r">Sortino</th>
                    <th class="r">Max DD</th>
                    <th class="r">Turnover</th>
                    <th class="r">$/cycle</th>
                    <th class="r">Council α</th>
                  </tr>
                </thead>
                <tbody>
                  @for (s of strategies(); track s.strategy) {
                    <tr>
                      <td>
                        {{ s.strategy_name }}
                        @if (s.provisional) {
                          <span class="badge" title="Fewer than 20 cycles — provisional">prov.</span>
                        }
                      </td>
                      <td class="muted">{{ s.flavor_display }}</td>
                      <td class="r">{{ s.n_cycles }}</td>
                      <td class="r">{{ num(s.sharpe) }}</td>
                      <td class="r">{{ num(s.sortino) }}</td>
                      <td class="r">{{ pctRaw(s.max_drawdown_pct) }}</td>
                      <td class="r">{{ pctRaw(s.annualised_turnover_pct) }}</td>
                      <td class="r">{{ usd(s.avg_cost_per_cycle_usd) }}</td>
                      <td class="r">
                        @if (s.council_alpha_bps !== null) {
                          <span
                            [class.pos]="num0(s.council_alpha_bps) >= 0"
                            [class.neg]="num0(s.council_alpha_bps) < 0"
                            [title]="councilTip(s)"
                            >{{ bps(s.council_alpha_bps) }}</span
                          >
                          <div class="micro">
                            {{ usd0(s.council_net_value_usd) }} · cost
                            {{ usd0(s.council_cost_usd) }}
                          </div>
                        } @else {
                          <span
                            class="muted"
                            title="Needs 30 days of baseline cycles before council-alpha is meaningful"
                            >—</span
                          >
                        }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>

        <section class="card">
          <div class="card-hd"><h2 class="title">Flavor benchmarks</h2></div>
          <p class="sub" style="margin: -4px 0 10px">
            Median across your own strategies of each flavor (single-tenant).
          </p>
          @if (flavors().length === 0) {
            <p class="empty">No flavor aggregates yet.</p>
          } @else {
            <div class="tbl-scroll">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>Flavor</th>
                    <th class="r">Strategies</th>
                    <th class="r">Median Sharpe</th>
                    <th class="r">Median Sortino</th>
                    <th class="r">Median Max DD</th>
                  </tr>
                </thead>
                <tbody>
                  @for (f of flavors(); track f.flavor) {
                    <tr>
                      <td>{{ f.flavor_display }}</td>
                      <td class="r">{{ f.n_cycles }}</td>
                      <td class="r">{{ num(f.sharpe) }}</td>
                      <td class="r">{{ num(f.sortino) }}</td>
                      <td class="r">{{ pctRaw(f.max_drawdown_pct) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </section>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:16px; }
      .sub { color: var(--text-3); font-size: 12.5px; margin-top: 4px; max-width: 640px; }
      .head-right { display:flex; gap:8px; align-items:center; }
      .sel { padding:6px 10px; border-radius: var(--r-6); background: var(--surface-2); color: var(--text-1); border:1px solid var(--border); }
      .sel.sm { padding:4px 8px; font-size:12px; }
      .card-hd { display:flex; align-items:center; justify-content:space-between; gap:12px; }
      .tbl .pos { color: var(--pos, #22c55e); }
      .tbl .neg { color: var(--neg, #ef4444); }
      .tbl .micro { font-size:10.5px; color: var(--text-3); margin-top:2px; white-space:nowrap; }
      .tabs { display:flex; gap:4px; margin-bottom:14px; border-bottom:1px solid var(--border); }
      .tab { padding:8px 14px; background:none; border:none; color: var(--text-3); cursor:pointer; border-bottom:2px solid transparent; }
      .tab.active { color: var(--text-1); border-bottom-color: var(--accent); }
      .card { margin-bottom: 16px; }
      .empty { color: var(--text-3); font-size: 13px; padding: 8px 2px; }
      .badge { font-size:10px; padding:1px 6px; border-radius:8px; background: var(--surface-3); color: var(--text-3); margin-left:6px; }
      .tbl .r { text-align: right; }
      .tbl .muted { color: var(--text-3); }
      .tbl .clk { cursor: pointer; }
      .tbl .clk:hover { background: var(--surface-2); }
    `,
  ],
})
export class LeaderboardPage implements OnInit {
  private readonly api = inject(ApiClient);

  readonly tab = signal<'agents' | 'strategies'>('agents');
  readonly window = signal<'30d' | '90d' | 'lifetime'>('90d');
  readonly stratSort = signal<'sharpe' | 'council_alpha_bps' | 'total_return_pct'>('sharpe');
  readonly busy = signal(false);

  readonly agents = signal<AgentRow[]>([]);
  readonly models = signal<ModelRow[]>([]);
  readonly strategies = signal<StrategyRow[]>([]);
  readonly flavors = signal<StrategyRow[]>([]);
  readonly drillAgent = signal<string | null>(null);
  readonly decisions = signal<DecisionRow[]>([]);

  ngOnInit(): void {
    this.load();
  }

  onWindow(w: string): void {
    this.window.set(w as '30d' | '90d' | 'lifetime');
    this.load();
  }

  onStratSort(sort: string): void {
    this.stratSort.set(sort as 'sharpe' | 'council_alpha_bps' | 'total_return_pct');
    this.loadStrategies();
  }

  load(): void {
    const w = this.window();
    this.api
      .get<{ rows: AgentRow[] }>(`/leaderboard/agents/?window=${w}`)
      .subscribe((r) => this.agents.set(r.rows ?? []));
    this.api
      .get<{ rows: ModelRow[] }>(`/leaderboard/models/?window=${w}`)
      .subscribe((r) => this.models.set(r.rows ?? []));
    this.loadStrategies();
    this.api
      .get<{ rows: StrategyRow[] }>(`/leaderboard/strategies/by-flavor/?window=${w}`)
      .subscribe((r) => this.flavors.set(r.rows ?? []));
  }

  loadStrategies(): void {
    this.api
      .get<{ rows: StrategyRow[] }>(
        `/leaderboard/strategies/?window=${this.window()}&sort=${this.stratSort()}`,
      )
      .subscribe((r) => this.strategies.set(r.rows ?? []));
  }

  drill(agent: string): void {
    this.drillAgent.set(agent);
    this.api
      .get<{ decisions: DecisionRow[] }>(
        `/leaderboard/agents/${agent}/decisions/?window=${this.window()}`,
      )
      .subscribe((r) => this.decisions.set(r.decisions ?? []));
  }

  openRun(id: number): void {
    window.location.assign(`/runs/${id}`);
  }

  recompute(): void {
    this.busy.set(true);
    this.api.post('/leaderboard/recompute/', {}).subscribe({
      next: () => {
        this.busy.set(false);
        this.load();
      },
      error: () => this.busy.set(false),
    });
  }

  pct(v: string | null): string {
    return v === null ? '—' : (Number(v) * 100).toFixed(1) + '%';
  }
  pctRaw(v: string | null): string {
    return v === null ? '—' : Number(v).toFixed(2) + '%';
  }
  num(v: string | null): string {
    return v === null ? '—' : Number(v).toFixed(2);
  }
  usd(v: string | null): string {
    return v === null ? '—' : '$' + Number(v).toFixed(4);
  }
  num0(v: string | null): number {
    return v === null ? 0 : Number(v);
  }
  bps(v: string | null): string {
    if (v === null) return '—';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + Math.round(n).toLocaleString() + ' bps';
  }
  usd0(v: string | null): string {
    if (v === null) return '—';
    const n = Number(v);
    return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  councilTip(s: StrategyRow): string {
    return (
      `Annualised return vs the council-free baseline. ` +
      `Council net value ${this.usd0(s.council_net_value_usd)} ` +
      `(after ${this.usd0(s.council_cost_usd)} of LLM cost) · ` +
      `baseline ${s.baseline_version || 'n/a'}`
    );
  }
}
