import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { CycleDetail } from '../../core/models/strategy.model';

@Component({
  selector: 'hf-strategies-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, DatePipe, DecimalPipe, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies', link:'/strategies'}, {label: store.currentStrategy()?.name || ''}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Strategy</div>
          <h1 style="margin-top:6px">{{ store.currentStrategy()?.name ?? 'Strategy' }}</h1>
          <p style="font-size:13px;color:var(--text-2);margin-top:4px">
            Universe {{ store.currentStrategy()?.universe_name }} ·
            Gross {{ store.currentStrategy()?.target_gross_pct }} ·
            Net {{ store.currentStrategy()?.target_net_pct }} ·
            K {{ store.currentStrategy()?.top_k_longs }}L / {{ store.currentStrategy()?.top_k_shorts }}S
          </p>
        </div>
        <div class="head-actions">
          <button class="btn primary" (click)="openEstimate()" [disabled]="running() || estimating()" data-test="run-now">
            {{ running() ? 'Dispatching…' : estimating() ? 'Estimating…' : 'Run cycle now' }}
          </button>
          <a class="btn ghost" routerLink="/strategies">All strategies</a>
        </div>
      </div>

      @if (estimate(); as est) {
        <div style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:var(--z-modal);display:flex;align-items:center;justify-content:center;padding:16px"
             (click)="cancelEstimate()">
          <div class="card" style="max-width:640px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:var(--shadow-3)"
               (click)="$event.stopPropagation()">
            <div class="card-hd"><span class="title">Confirm cycle dispatch</span></div>
            <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
              <p style="font-size:11.5px;color:var(--text-3);margin:0">
                Preset <span class="mono" style="color:var(--text)">{{ est.preset }}</span> ·
                {{ est.n_candidates }} candidates · full council per candidate
              </p>

              <div
                style="border:1px solid;border-radius:6px;padding:12px;display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:13px"
                [style.borderColor]="est.exceeds_ceiling ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'"
                [style.background]="est.exceeds_ceiling ? 'var(--acc-short-soft)' : 'var(--acc-long-soft)'">
                <div>Est. per-call: <span class="mono">$ {{ est.per_call_usd.toFixed(4) }}</span></div>
                <div>Est. total:
                  <span class="mono" style="font-weight:600"
                    [style.color]="est.exceeds_ceiling ? 'var(--acc-short-fg)' : 'var(--text)'">
                    $ {{ est.est_total_usd.toFixed(2) }}
                  </span>
                </div>
                <div>Cost ceiling: <span class="mono">$ {{ est.cost_ceiling_usd.toFixed(2) }}</span></div>
                <div>n_candidates: <span class="mono">{{ est.n_candidates }}</span></div>
              </div>

              @if (est.exceeds_ceiling) {
                <p style="font-size:11.5px;color:var(--acc-short-fg);margin:0">
                  ⚠ Estimate exceeds your cost ceiling. The cycle will trim K_longs/K_shorts before dispatching — raise the ceiling for a full fan-out.
                </p>
              }

              <div class="eyebrow">Per-agent model assignment</div>
              <table class="tbl">
                <thead><tr>
                  <th>Agent</th><th>Model</th><th>Tier</th><th class="right">$/call</th>
                </tr></thead>
                <tbody>
                  @for (row of est.per_agent; track row.agent) {
                    <tr [style.color]="row.model.startsWith('anthropic') ? 'var(--acc-short-fg)' : null">
                      <td class="mono">{{ row.agent }}</td>
                      <td class="mono">{{ row.model_name || row.model }}</td>
                      <td>{{ row.tier }}</td>
                      <td class="num">{{ row.per_call_usd.toFixed(4) }}</td>
                    </tr>
                  }
                </tbody>
              </table>

              @if (anyAnthropic(est)) {
                <p style="font-size:11.5px;color:var(--acc-short-fg);margin:0">
                  ⚠ This cycle will hit Anthropic for at least one agent. If you didn't intend this, change the model in
                  <a routerLink="/settings/models" style="text-decoration:underline">Settings → Models</a>
                  or pick a different strategy preset.
                </p>
              }

              <div style="display:flex;justify-content:flex-end;gap:8px;border-top:1px solid var(--border);padding-top:12px">
                <button class="btn" (click)="cancelEstimate()">Cancel</button>
                <button class="btn primary" (click)="confirmRun()" [disabled]="running()" data-test="confirm-run"
                  [style.background]="est.exceeds_ceiling ? 'var(--acc-hold)' : null"
                  [style.borderColor]="est.exceeds_ceiling ? 'var(--acc-hold)' : null">
                  {{ running() ? 'Dispatching…' : 'Confirm & run cycle' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      }

      @if (notice()) {
        <div class="pill info" style="margin-bottom:14px;height:auto;padding:8px 12px">
          <span class="dot"></span>{{ notice() }}
        </div>
      }

      <div style="display:grid;grid-template-columns:280px 1fr;gap:18px">
        <section class="card">
          <div class="card-hd"><span class="title">Cycles</span></div>
          <div class="card-bd">
            @if (store.cycles().length === 0) {
              <p style="font-size:11.5px;color:var(--text-3);margin:0">No cycles yet — click "Run cycle now".</p>
            } @else {
              <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px;font-size:13px">
                @for (c of store.cycles(); track c.id) {
                  <li>
                    <button (click)="openCycle(c.id)"
                      style="background:transparent;border:0;padding:0;color:var(--acc-info-fg);cursor:pointer;text-align:left">
                      <span class="mono">{{ c.as_of_date }}</span> · {{ c.status }} · g {{ c.gross_pct }}
                    </button>
                  </li>
                }
              </ul>
            }
          </div>
        </section>

        @if (cycle(); as c) {
          <section class="card">
            <div class="card-hd">
              <span class="title">Cycle {{ c.as_of_date }}</span>
              <span class="pill"
                [class.ok]="c.status==='done'"
                [class.warn]="c.status==='running' || c.status==='queued'"
                [class.err]="c.status==='failed'">
                <span class="dot"></span>{{ c.status }}
              </span>
              <div class="actions">
                <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                  {{ c.finished_at ? (c.finished_at | date: 'short') : '—' }}
                </span>
                <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                  · gross {{ c.gross_pct | number: '1.4-4' }} · net {{ c.net_pct | number: '1.4-4' }}
                </span>
              </div>
            </div>
            @if (store.currentStrategy()?.kind === 'market_neutral') {
              <div class="card-bd" style="display:flex;gap:14px;align-items:center;border-top:1px solid var(--border);padding-top:10px">
                <span class="eyebrow">Neutrality</span>
                <span class="mono" [style.color]="netNeutral(c) ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                  net $ {{ c.realised_net_pct || c.net_pct | number: '1.4-4' }}
                </span>
                <span class="mono" [style.color]="betaNeutral(c) ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                  β {{ c.realised_portfolio_beta | number: '1.3-3' }}
                </span>
                @if (c.beta_diagnostics?.['alpha_clamped']) {
                  <span class="mono" style="color:var(--acc-short-fg);font-size:11.5px">⚠ α clamped — partial breach</span>
                }
                @if (unreliable(c).length) {
                  <span class="mono" style="font-size:11.5px;color:var(--text-3)">
                    unreliable β: {{ unreliable(c).join(', ') }}
                  </span>
                }
              </div>
            }
            <div class="card-bd" style="display:flex;flex-direction:column;gap:18px">
              @if (c.error_message) {
                <p style="color:var(--acc-short-fg);font-size:12px;margin:0">{{ c.error_message }}</p>
              }

              <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
                <div>
                  <div class="eyebrow" style="color:var(--acc-long-fg);margin-bottom:6px">Long book</div>
                  @if (longs().length === 0) {
                    <p style="font-size:11.5px;color:var(--text-3);margin:0">No longs.</p>
                  } @else {
                    <table class="tbl">
                      <tbody>
                        @for (row of longs(); track row.ticker) {
                          <tr>
                            <td class="mono" style="color:var(--text)">{{ row.ticker }}</td>
                            <td class="num">{{ row.weight | number: '1.2-2' }}%</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  }
                </div>
                <div>
                  <div class="eyebrow" style="color:var(--acc-short-fg);margin-bottom:6px">Short book</div>
                  @if (shorts().length === 0) {
                    <p style="font-size:11.5px;color:var(--text-3);margin:0">No shorts.</p>
                  } @else {
                    <table class="tbl">
                      <tbody>
                        @for (row of shorts(); track row.ticker) {
                          <tr>
                            <td class="mono" style="color:var(--text)">{{ row.ticker }}</td>
                            <td class="num">{{ row.weight | number: '1.2-2' }}%</td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  }
                </div>
              </div>

              <div>
                <div class="eyebrow" style="margin-bottom:6px">Sector exposure (signed)</div>
                <table class="tbl">
                  <tbody>
                    @for (row of sectorRows(); track row.sector) {
                      <tr>
                        <td>{{ row.sector || '—' }}</td>
                        <td class="num"
                          [style.color]="row.weight > 0 ? 'var(--acc-long-fg)' : row.weight < 0 ? 'var(--acc-short-fg)' : null">
                          {{ row.weight | number: '1.2-2' }}%
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>

              <div>
                <div class="eyebrow" style="margin-bottom:6px">Rebalance orders ({{ c.orders.length }})</div>
                @if (c.orders.length === 0) {
                  <p style="font-size:11.5px;color:var(--text-3);margin:0">No orders this cycle.</p>
                } @else {
                  <table class="tbl">
                    <thead><tr>
                      <th>Seq</th><th>Side</th><th>Ticker</th>
                      <th class="right">Qty</th><th class="right">Limit</th>
                      <th>Reason</th><th class="right">Notional</th>
                    </tr></thead>
                    <tbody>
                      @for (o of c.orders; track o.id) {
                        <tr>
                          <td class="mono">{{ o.sequence }}</td>
                          <td class="mono"
                            [style.color]="(o.side === 'buy' || o.side === 'cover') ? 'var(--acc-long-fg)' : (o.side === 'sell' || o.side === 'short') ? 'var(--acc-short-fg)' : null">
                            {{ o.side }}
                          </td>
                          <td class="mono" style="color:var(--text)">{{ o.ticker }}</td>
                          <td class="num">{{ o.quantity }}</td>
                          <td class="num">{{ o.limit_price ?? 'mkt' }}</td>
                          <td style="font-size:11.5px;color:var(--text-2)">{{ o.reason }}</td>
                          <td class="num">$ {{ o.estimated_notional_usd }}</td>
                        </tr>
                      }
                    </tbody>
                  </table>
                }
              </div>

              @if (c.rejected_candidates.length > 0) {
                <details>
                  <summary class="eyebrow" style="cursor:pointer">
                    Rejected candidates ({{ c.rejected_candidates.length }})
                  </summary>
                  <ul class="mono" style="margin:8px 0 0;padding:0;list-style:none;display:flex;flex-direction:column;gap:4px;font-size:11.5px;color:var(--text-2)">
                    @for (r of c.rejected_candidates; track $index) {
                      <li><span style="color:var(--text)">{{ r.ticker ?? '(sector)' }}</span> — {{ r.reason }}</li>
                    }
                  </ul>
                </details>
              }
            </div>
          </section>
        }
      </div>
    </hf-app-shell>
  `,
})
export class StrategiesDetailPage implements OnInit, OnDestroy {
  readonly store = inject(StrategiesStore);
  private readonly route = inject(ActivatedRoute);
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  running = signal(false);
  estimating = signal(false);
  estimate = signal<{
    n_candidates: number;
    per_call_usd: number;
    est_total_usd: number;
    cost_ceiling_usd: number;
    exceeds_ceiling: boolean;
    per_agent: { agent: string; model: string; model_name: string; tier: string; per_call_usd: number }[];
    overrides: Record<string, string>;
    preset: string;
  } | null>(null);
  notice = signal<string | null>(null);
  cycle = signal<CycleDetail | null>(null);

  openEstimate(): void {
    this.estimating.set(true);
    this.notice.set(null);
    this.store.estimate(this.strategyId).subscribe({
      next: (e) => { this.estimating.set(false); this.estimate.set(e); },
      error: () => { this.estimating.set(false); this.notice.set('Failed to fetch cost estimate.'); },
    });
  }
  cancelEstimate(): void { this.estimate.set(null); }
  anyAnthropic(est: { per_agent: { model: string }[] }): boolean {
    return est.per_agent.some((r) => r.model.startsWith('anthropic'));
  }
  confirmRun(): void { this.estimate.set(null); this.runNow(); }

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
  netNeutral(c: CycleDetail): boolean {
    const tol = Number(this.store.currentStrategy()?.neutrality_tolerance_dollar_pct ?? 0.02);
    return Math.abs(Number(c.realised_net_pct ?? c.net_pct)) <= tol;
  }
  unreliable(c: CycleDetail): string[] {
    const u = (c.beta_diagnostics as Record<string, unknown> | undefined)?.['unreliable'];
    return Array.isArray(u) ? (u as string[]) : [];
  }
  betaNeutral(c: CycleDetail): boolean {
    const tol = Number(this.store.currentStrategy()?.neutrality_tolerance_beta ?? 0.05);
    return Math.abs(Number(c.realised_portfolio_beta ?? 0)) <= tol;
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
  ngOnDestroy(): void { if (this.pollHandle) clearInterval(this.pollHandle); }

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
    this.store.cycleDetail(this.strategyId, id).subscribe((d) => this.cycle.set(d));
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
      error: () => { this.running.set(false); this.notice.set('Failed to dispatch cycle.'); },
    });
  }
}
