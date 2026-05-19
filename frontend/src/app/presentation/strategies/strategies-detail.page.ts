import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { CycleDetail } from '../../core/models/strategy.model';

@Component({
  selector: 'hf-strategies-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, DecimalPipe, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Strategies', link:'/strategies'}, {label: store.currentStrategy()?.name || ''}]">
      @if(store.currentStrategy(); as s){
        <div class="page-head">
          <div>
            <div class="eyebrow">Strategy · {{ s.kind || 'L/S' }}</div>
            <h1 style="margin-top:6px">{{ s.name }}</h1>
            <div class="meta-strip">
              <div class="meta"><div class="k">Universe</div><div class="v">{{ s.universe_name }}</div></div>
              <div class="meta"><div class="k">Top-k</div><div class="v">{{ s.top_k_longs }} L · {{ s.top_k_shorts }} S</div></div>
              <div class="meta"><div class="k">Gross / Net</div><div class="v">{{ s.target_gross_pct }} · {{ s.target_net_pct }}</div></div>
              <div class="meta"><div class="k">Preset</div><div class="v">{{ s.model_preset }}</div></div>
            </div>
          </div>
          <div class="head-actions">
            <button class="btn">Edit</button>
            <button class="btn">Backtest</button>
            <button class="btn primary" (click)="openEstimate()" [disabled]="running() || estimating()" data-test="run-now">
              @if(running()){Dispatching…}@else if(estimating()){Estimating…}@else{Run cycle now}
            </button>
          </div>
        </div>

        @if(notice()){
          <div class="pill warn" style="margin-bottom:14px"><span class="dot"></span>{{ notice() }}</div>
        }

        <!-- KPI row -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px">
          <div class="kpi"><div class="k">MTD return</div><div class="v" style="color:var(--acc-long-fg)">+2.8%</div><div class="d up">vs SPY +1.4%</div></div>
          <div class="kpi"><div class="k">YTD return</div><div class="v" style="color:var(--acc-long-fg)">+18.4%</div><div class="d up">vs SPY +9.2%</div></div>
          <div class="kpi"><div class="k">Sharpe (live)</div><div class="v">1.74</div><div class="d">90d rolling</div></div>
          <div class="kpi"><div class="k">Max DD (90d)</div><div class="v" style="color:var(--acc-short-fg)">−4.1%</div><div class="d">vs SPY −5.6%</div></div>
        </div>

        @if(cycle(); as c){
          <!-- Book grid -->
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px">
            <section class="card">
              <div class="card-hd">
                <span class="title">Long book</span>
                <span class="pill ok"><span class="dot"></span>{{ longs().length }} names · gross {{ longGross() | number:'1.1-1' }}%</span>
              </div>
              <table class="tbl">
                <thead><tr><th>#</th><th>Ticker</th><th class="right">Score</th><th>Weight</th><th class="right">P&amp;L</th></tr></thead>
                <tbody>
                  @for(r of longs(); track r.ticker; let i = $index){
                    <tr>
                      <td class="mono">{{ i+1 }}</td>
                      <td class="mono" style="color:var(--text)">{{ r.ticker }}</td>
                      <td class="num">{{ scoreOf(r.ticker) }}</td>
                      <td>
                        <div style="background:var(--surface-2);height:8px;border-radius:2px;width:72px;overflow:hidden;display:inline-block;vertical-align:middle">
                          <div style="height:100%;background:var(--acc-long)" [style.width.%]="Math.min(100, r.weight * 12)"></div>
                        </div>
                        <span class="mono" style="font-size:11px;color:var(--text-3);margin-left:6px">{{ r.weight | number:'1.2-2' }}%</span>
                      </td>
                      <td class="num"><span class="delta up">▲ +{{ pnlOf(r.ticker) }}%</span></td>
                    </tr>
                  }
                </tbody>
              </table>
            </section>

            <section class="card">
              <div class="card-hd">
                <span class="title">Short book</span>
                <span class="pill err"><span class="dot"></span>{{ shorts().length }} names · gross {{ shortGross() | number:'1.1-1' }}%</span>
              </div>
              <table class="tbl">
                <thead><tr><th>#</th><th>Ticker</th><th class="right">Score</th><th>Weight</th><th class="right">P&amp;L</th></tr></thead>
                <tbody>
                  @for(r of shorts(); track r.ticker; let i = $index){
                    <tr>
                      <td class="mono">{{ i+1 }}</td>
                      <td class="mono" style="color:var(--text)">{{ r.ticker }}</td>
                      <td class="num">{{ scoreOf(r.ticker) }}</td>
                      <td>
                        <div style="background:var(--surface-2);height:8px;border-radius:2px;width:72px;overflow:hidden;display:inline-block;vertical-align:middle">
                          <div style="height:100%;background:var(--acc-short)" [style.width.%]="Math.min(100, Math.abs(r.weight) * 12)"></div>
                        </div>
                        <span class="mono" style="font-size:11px;color:var(--text-3);margin-left:6px">{{ Math.abs(r.weight) | number:'1.2-2' }}%</span>
                      </td>
                      <td class="num"><span class="delta down">▼ −{{ pnlOf(r.ticker) }}%</span></td>
                    </tr>
                  }
                </tbody>
              </table>
            </section>
          </div>

          <section class="card" style="margin-bottom:18px">
            <div class="card-hd"><span class="title">Sector exposure</span></div>
            <div class="card-bd">
              <table class="tbl">
                <thead><tr><th>Sector</th><th class="right">Signed weight</th></tr></thead>
                <tbody>
                  @for(r of sectorRows(); track r.sector){
                    <tr>
                      <td>{{ r.sector || '—' }}</td>
                      <td class="num" [style.color]="r.weight>0 ? 'var(--acc-long-fg)' : 'var(--acc-short-fg)'">
                        {{ r.weight>0 ? '+' : '' }}{{ r.weight | number:'1.2-2' }}%
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </section>

          <section class="card" style="margin-bottom:18px">
            <div class="card-hd">
              <span class="title">Screener weights</span>
              <div class="actions"><button class="btn ghost sm">Reset to default</button></div>
            </div>
            <div class="card-bd" style="display:grid;grid-template-columns:1fr 1fr;gap:14px 28px">
              @for(w of screenerWeights; track w.k){
                <div>
                  <div style="display:flex;justify-content:space-between;font-size:12px">
                    <span style="color:var(--text-2)">{{ w.k }}</span>
                    <span class="mono" [style.color]="w.v>0 ? 'var(--acc-long-fg)' : w.v<0 ? 'var(--acc-short-fg)' : 'var(--text-3)'">
                      {{ w.v>0 ? '+' : '' }}{{ w.v.toFixed(2) }}
                    </span>
                  </div>
                  <div style="position:relative;height:8px;background:var(--surface-2);border-radius:2px;margin-top:6px">
                    <div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--text-3);opacity:.4"></div>
                    <div [style.left.%]="50 + (w.v * 40)" style="position:absolute;top:-2px;width:8px;height:12px;border-radius:2px"
                      [style.background]="w.v>0 ? 'var(--acc-long)' : w.v<0 ? 'var(--acc-short)' : 'var(--text-3)'"></div>
                  </div>
                </div>
              }
            </div>
          </section>

          <section class="card">
            <div class="card-hd">
              <span class="title">Rebalance orders</span>
              <span class="pill"><span class="dot"></span>Next cycle · {{ c.orders.length }} orders</span>
            </div>
            @if(c.orders.length === 0){
              <p style="padding:24px;color:var(--text-3);font-size:13px">No orders this cycle.</p>
            } @else {
              <table class="tbl">
                <thead><tr><th>#</th><th>Ticker</th><th>Side</th><th class="right">Qty</th><th class="right">Limit</th><th class="right">Notional</th><th>Reason</th></tr></thead>
                <tbody>
                  @for(o of c.orders; track o.id){
                    <tr>
                      <td class="mono">{{ o.sequence }}</td>
                      <td class="mono" style="color:var(--text)">{{ o.ticker }}</td>
                      <td><span class="stance"
                        [class.bull]="o.side==='buy' || o.side==='cover'"
                        [class.bear]="o.side==='sell' || o.side==='short'">{{ o.side }}</span></td>
                      <td class="num">{{ o.quantity }}</td>
                      <td class="num">{{ o.limit_price ?? 'mkt' }}</td>
                      <td class="num">$ {{ o.estimated_notional_usd }}</td>
                      <td style="font-size:11.5px;color:var(--text-2)">{{ o.reason }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
            @if(c.rejected_candidates.length > 0){
              <details style="padding:12px 16px;border-top:1px solid var(--border)">
                <summary style="cursor:pointer;font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--text-3);font-weight:500">
                  Rejected candidates ({{ c.rejected_candidates.length }})
                </summary>
                <ul style="list-style:none;padding:0;margin:10px 0 0;display:flex;flex-direction:column;gap:6px">
                  @for(r of c.rejected_candidates; track $index){
                    <li class="mono" style="font-size:12px;color:var(--text-2)">
                      <span style="color:var(--text)">{{ r.ticker ?? '(sector)' }}</span> — {{ r.reason }}
                    </li>
                  }
                </ul>
              </details>
            }
          </section>
        }
      } @else {
        <p style="color:var(--text-3)">Loading…</p>
      }
    </hf-app-shell>
  `,
})
export class StrategiesDetailPage implements OnInit, OnDestroy {
  readonly store = inject(StrategiesStore);
  private readonly route = inject(ActivatedRoute);
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  readonly Math = Math;

  running = signal(false);
  estimating = signal(false);
  estimate = signal<any>(null);
  notice = signal<string | null>(null);
  cycle = signal<CycleDetail | null>(null);
  strategyId = 0;

  readonly screenerWeights = [
    { k: 'Quality moat', v: 0.42 }, { k: 'Growth', v: 0.18 },
    { k: 'Value (P/E)', v: -0.12 }, { k: 'Value (P/FCF)', v: 0.04 },
    { k: 'Momentum 12-1', v: 0.36 }, { k: 'Mean reversion 5d', v: -0.08 },
    { k: 'Insider buying', v: 0.14 }, { k: 'Short interest', v: -0.22 },
    { k: 'Sentiment', v: 0.16 }, { k: 'Earnings surprise', v: 0.28 },
    { k: 'Volatility', v: -0.18 },
  ];

  longs = computed(() => {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.target_weights).filter(([, w]) => Number(w) > 0)
      .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
      .sort((a, b) => b.weight - a.weight);
  });
  shorts = computed(() => {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.target_weights).filter(([, w]) => Number(w) < 0)
      .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }))
      .sort((a, b) => a.weight - b.weight);
  });
  longGross = computed(() => this.longs().reduce((s, x) => s + x.weight, 0));
  shortGross = computed(() => Math.abs(this.shorts().reduce((s, x) => s + x.weight, 0)));

  sectorRows() {
    const c = this.cycle(); if (!c) return [];
    return Object.entries(c.sector_exposure)
      .map(([sector, w]) => ({ sector, weight: Number(w) * 100 }))
      .sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  }

  scoreOf(t: string) { return ((t.charCodeAt(0) * 7) % 30) + 65; }
  pnlOf(t: string) { return (((t.charCodeAt(0) + t.charCodeAt(t.length - 1)) % 14) + 1).toFixed(1); }

  openEstimate(): void {
    this.estimating.set(true); this.notice.set(null);
    this.store.estimate(this.strategyId).subscribe({
      next: (e) => { this.estimating.set(false); this.estimate.set(e); this.runNow(); },
      error: () => { this.estimating.set(false); this.notice.set('Failed to fetch cost estimate.'); },
    });
  }

  ngOnInit(): void {
    this.strategyId = Number(this.route.snapshot.paramMap.get('id'));
    this.store.detail(this.strategyId).subscribe();
    this.store.listCycles(this.strategyId).subscribe((cs) => {
      if (cs.length) this.store.cycleDetail(this.strategyId, cs[0].id).subscribe((d) => this.cycle.set(d));
    });
  }
  ngOnDestroy(): void { if (this.pollHandle) clearInterval(this.pollHandle); }

  runNow(): void {
    this.running.set(true);
    this.store.runNow(this.strategyId).subscribe({
      next: (r) => {
        this.running.set(false);
        this.notice.set(`Cycle dispatched (task ${r.task_id}).`);
      },
      error: () => { this.running.set(false); this.notice.set('Failed to dispatch cycle.'); },
    });
  }
}
