import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { AuthStore } from '../../abstraction/auth.store';
import { MacroStore } from '../../abstraction/macro.store';
import { RunsStore } from '../../abstraction/runs.store';
import { StrategiesStore } from '../../abstraction/strategies.store';

interface BookRow {
  ticker: string; name: string; sector: string; side: 'L' | 'S';
  weight: number; mkt: number; pnl: number; avg: number; spark: number[];
}

@Component({
  selector: 'hf-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, AppShellComponent],
  template: `
    <hf-app-shell [crumbs]="[{label:'Dashboard'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">paper account · GMT-04 · market open</div>
          <h1 style="margin-top:6px">Good afternoon, Elena</h1>
          <div class="meta-strip">
            <div class="meta"><div class="k">Strategy</div><div class="v">Concentrated L/S · v3.2</div></div>
            <div class="meta"><div class="k">Universe</div><div class="v">S&amp;P 500 · 503 names</div></div>
            <div class="meta"><div class="k">Next rebalance</div><div class="v">2026-05-23 · T+3</div></div>
            <div class="meta"><div class="k">Monthly burn</div><div class="v">$ 412.10 / 800</div></div>
          </div>
        </div>
        <div class="head-actions">
          <a class="btn" routerLink="/backtests/new"><svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New backtest</a>
          <a class="btn primary" routerLink="/runs/new"><svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New analysis <span class="kbd">⌘N</span></a>
        </div>
      </div>

      <!-- KPI row -->
      <div style="display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr 1fr;gap:14px;margin-bottom:18px">
        <div class="card" style="grid-row: span 1">
          <div class="card-hd">
            <span class="title">Macro regime</span>
            <span class="pill info"><span class="dot"></span>RISK-ON · LATE-CYCLE</span>
          </div>
          <div class="card-bd" style="display:flex;gap:14px">
            <div style="position:relative;width:180px;height:180px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px">
              <div style="position:absolute;inset:0;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;font-family:var(--font-mono);font-size:10px;color:var(--text-3)">
                <div style="padding:6px;border-right:1px dashed var(--border);border-bottom:1px dashed var(--border)">Recovery</div>
                <div style="padding:6px;border-bottom:1px dashed var(--border);text-align:right">Expansion</div>
                <div style="padding:6px;border-right:1px dashed var(--border)">Recession</div>
                <div style="padding:6px;text-align:right">Slowdown</div>
              </div>
              <div style="position:absolute;left:66%;top:34%;width:10px;height:10px;border-radius:50%;background:var(--acc-info);box-shadow:0 0 0 4px var(--acc-info-soft);transform:translate(-50%,-50%)"></div>
              @for(t of trail; track $index){
                <div style="position:absolute;width:6px;height:6px;border-radius:50%;background:var(--text-3);opacity:.5;transform:translate(-50%,-50%)"
                  [style.left]="t[0]+'%'" [style.top]="t[1]+'%'"></div>
              }
              <div style="position:absolute;bottom:-18px;left:0;font-family:var(--font-mono);font-size:10px;color:var(--text-3)">growth →</div>
              <div style="position:absolute;top:0;right:-18px;font-family:var(--font-mono);font-size:10px;color:var(--text-3);writing-mode:vertical-rl">policy ↓</div>
            </div>
            <div style="flex:1;display:flex;flex-direction:column;gap:8px">
              <div style="display:flex;flex-wrap:wrap;gap:6px">
                <span class="pill ok"><span class="dot"></span>growth · expansion</span>
                <span class="pill warn"><span class="dot"></span>inflation · moderate</span>
                <span class="pill"><span class="dot"></span>curve · normal</span>
                <span class="pill info"><span class="dot"></span>policy · easing</span>
              </div>
              <p style="font-size:12.5px;color:var(--text-2);line-height:18px;margin:0">
                Growth momentum positive into Q3; services PMI 54.2, manufacturing 51.1. Disinflation pace slowing, core PCE 2.8% YoY.
              </p>
              <a href="#" style="font-size:12px;color:var(--acc-info-fg);margin-top:auto">Read narrative →</a>
            </div>
          </div>
        </div>

        <div class="kpi">
          <div class="k">NAV</div>
          <div class="v">$ 124.83 M</div>
          <div class="d up">▲ $ 483K · +0.39% day</div>
          <div class="sparkbar">
            @for(h of bars; track $index){
              <span [style.height]="h+'%'" [class.up]="h>50"></span>
            }
          </div>
        </div>

        <div class="kpi">
          <div class="k">Day P&amp;L</div>
          <div class="v" style="color:var(--acc-long-fg)">+ $ 483,209</div>
          <div class="d up">+0.39% vs SPY +0.21%</div>
          <div class="sparkbar">
            @for(h of bars; track $index){
              <span [style.height]="h+'%'" [class.up]="h>50" [class.down]="h<30"></span>
            }
          </div>
        </div>

        <div class="kpi">
          <div class="k">Gross · Net</div>
          <div class="v">142.8 · 38.4%</div>
          <div class="d">L 90.6 · S −52.2 (target 150 · 40)</div>
          <div style="display:flex;height:8px;background:var(--surface-2);border-radius:2px;overflow:hidden;margin-top:4px">
            <div style="width:34.8%;background:var(--acc-short);height:100%"></div>
            <div style="width:1px;background:var(--text-3)"></div>
            <div style="width:60.4%;background:var(--acc-long);height:100%"></div>
          </div>
        </div>

        <div class="kpi">
          <div class="k">Positions</div>
          <div class="v">47 · <span style="color:var(--acc-long-fg);font-size:18px">+3</span></div>
          <div class="d">28 L · 19 S · avg hold 14d</div>
          <div class="sparkbar">
            @for(h of bars; track $index){
              <span [style.height]="h+'%'"></span>
            }
          </div>
        </div>
      </div>

      <!-- 2-col grid -->
      <div style="display:grid;grid-template-columns:1.4fr 1fr;gap:18px">
        <div style="display:flex;flex-direction:column;gap:18px">
          <section class="card">
            <div class="card-hd">
              <span class="title">Current book</span>
              <span class="pill"><span class="dot"></span>47 positions · as-of 2026-05-19 12:48 ET</span>
              <div class="actions">
                <button class="btn ghost sm"><svg width="12" height="12"><use href="/icons.svg#i-filter" /></svg> Filter</button>
                <button class="btn ghost sm">Open table →</button>
              </div>
            </div>
            <div style="max-height:380px;overflow:auto">
              <table class="tbl">
                <thead><tr><th>Ticker</th><th>Name</th><th>Sector</th><th>Side</th><th class="right">Weight</th><th class="right">Mkt val</th><th class="right">Unrealized</th><th class="right">Avg cost</th></tr></thead>
                <tbody>
                  @for(r of bookRows; track r.ticker){
                    <tr>
                      <td class="mono" style="color:var(--text)">{{ r.ticker }}</td>
                      <td>{{ r.name }}</td>
                      <td style="color:var(--text-2)">{{ r.sector }}</td>
                      <td>
                        <span class="stance" [class.bull]="r.side==='L'" [class.bear]="r.side==='S'">{{ r.side }}</span>
                      </td>
                      <td class="num">{{ r.side==='S' ? '−' : '+' }}{{ r.weight.toFixed(2) }}%</td>
                      <td class="num">$ {{ r.mkt.toLocaleString() }}</td>
                      <td class="num">
                        <span class="delta" [class.up]="r.pnl>0" [class.down]="r.pnl<0">
                          {{ r.pnl>0 ? '▲ +' : '▼ ' }}{{ r.pnl.toFixed(1) }}%
                        </span>
                      </td>
                      <td class="num">$ {{ r.avg.toFixed(2) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </section>

          <section class="card">
            <div class="card-hd">
              <span class="title">Sector exposure</span>
              <span class="pill"><span class="dot"></span>gross 142.8% · net 38.4%</span>
              <div class="actions"><span class="mono" style="font-size:11px;color:var(--text-3)">vs S&amp;P 500 weights</span></div>
            </div>
            <div class="card-bd">
              <div style="display:flex;height:18px;border:1px solid var(--border);border-radius:4px;overflow:hidden">
                <div style="width:14%;background:var(--acc-short);opacity:.75" title="Info Tech (S)"></div>
                <div style="width:12%;background:var(--c4);opacity:.75" title="Energy (S)"></div>
                <div style="width:10%;background:var(--c7);opacity:.75" title="Health (S)"></div>
                <div style="width:8%;background:var(--c5);opacity:.75" title="Financials (S)"></div>
                <div style="width:8%;background:var(--c3);opacity:.75" title="Discretionary (S)"></div>
                <div style="width:1px;background:var(--text)"></div>
                <div style="width:18%;background:var(--c2)" title="Info Tech"></div>
                <div style="width:12%;background:var(--c1)" title="Comm Services"></div>
                <div style="width:10%;background:var(--c6)" title="Health"></div>
                <div style="width:8%;background:var(--c8)" title="Financials"></div>
                <div style="flex:1;background:var(--c3)" title="Industrials"></div>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-top:14px;font-size:12px">
                @for(s of sectors; track s.name){
                  <div style="display:flex;align-items:center;gap:6px">
                    <span style="width:10px;height:10px;border-radius:2px" [style.background]="s.col"></span>
                    <span style="color:var(--text-2)">{{ s.name }}</span>
                    <span class="mono" style="margin-left:auto" [style.color]="s.pct<0?'var(--acc-short-fg)':'var(--text)'">{{ s.pct>0?'+':'' }}{{ s.pct.toFixed(1) }}%{{ s.pct<0?' (S)':'' }}</span>
                  </div>
                }
              </div>
            </div>
          </section>
        </div>

        <div style="display:flex;flex-direction:column;gap:18px">
          <section class="card">
            <div class="card-hd">
              <span class="title">Recent runs</span>
              <span class="pill info live"><span class="dot"></span>2 running</span>
              <div class="actions"><a routerLink="/runs/new" class="btn ghost sm">+ New</a></div>
            </div>
            <div>
              @for(it of feed; track it.id){
                <div style="display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--border)">
                  <span class="mono-tile" [class]="'mt-' + it.col">{{ it.mono }}</span>
                  <div style="display:flex;flex-direction:column;gap:2px">
                    <span style="font-size:13px"><span class="mono" style="color:var(--text)">{{ it.tickers }}</span></span>
                    <div style="display:flex;gap:8px;align-items:center;font-size:11px;color:var(--text-3)">
                      <span class="stance" [class.bull]="it.sig==='buy'" [class.bear]="it.sig==='sell'" [class.neut]="it.sig==='hold'">
                        {{ it.sigLabel }} · {{ it.conf }}
                      </span>
                      <span class="mono">#{{ it.id }} · {{ it.age }}</span>
                    </div>
                  </div>
                  @if(it.running){
                    <button class="icon-btn"><svg width="14" height="14"><use href="/icons.svg#i-x" /></svg></button>
                  } @else {
                    <span class="mono" style="font-size:12px;color:var(--text-3)">$ {{ it.cost }}</span>
                  }
                </div>
              }
            </div>
          </section>

          <section class="card">
            <div class="card-hd">
              <span class="title">Alerts</span>
              <span class="pill warn"><span class="dot"></span>3 active</span>
            </div>
            <div>
              <div style="display:grid;grid-template-columns:auto 1fr;gap:10px;padding:12px 14px;border-bottom:1px solid var(--border)">
                <svg width="16" height="16" style="color:var(--acc-hold)"><use href="/icons.svg#i-alert" /></svg>
                <div>
                  <div style="font-size:13px;font-weight:500">Stop loss within 1.4%</div>
                  <div class="mono" style="font-size:11.5px;color:var(--text-3);margin-top:2px">TSLA short · trigger −15% from cost · close $ 290.50</div>
                </div>
              </div>
              <div style="display:grid;grid-template-columns:auto 1fr;gap:10px;padding:12px 14px;border-bottom:1px solid var(--border)">
                <svg width="16" height="16" style="color:var(--acc-info)"><use href="/icons.svg#i-info" /></svg>
                <div>
                  <div style="font-size:13px;font-weight:500">Earnings within 2 days</div>
                  <div class="mono" style="font-size:11.5px;color:var(--text-3);margin-top:2px">NVDA · 2026-05-21 AMC · IV rank 88%</div>
                </div>
              </div>
              <div style="display:grid;grid-template-columns:auto 1fr;gap:10px;padding:12px 14px">
                <svg width="16" height="16" style="color:var(--acc-short)"><use href="/icons.svg#i-alert" /></svg>
                <div>
                  <div style="font-size:13px;font-weight:500">Sector cap breach pending</div>
                  <div class="mono" style="font-size:11.5px;color:var(--text-3);margin-top:2px">Info Tech 24.6% / 25% · next rebalance trims 0.6 pp</div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </hf-app-shell>
  `,
})
export class DashboardPage implements OnInit {
  readonly auth = inject(AuthStore);
  readonly runs = inject(RunsStore);
  readonly macro = inject(MacroStore);
  readonly strategies = inject(StrategiesStore);

  bars = [42, 55, 48, 60, 52, 68, 64, 72, 58, 70, 78, 84];
  trail: [number, number][] = [
    [42, 58], [48, 52], [54, 46], [58, 42], [62, 38],
  ];
  bookRows: BookRow[] = [
    { ticker: 'AAPL', name: 'Apple Inc.', sector: 'Info Tech', side: 'L', weight: 2.40, mkt: 2996000, pnl: 4.2, avg: 191.20, spark: [] },
    { ticker: 'MSFT', name: 'Microsoft Corp.', sector: 'Info Tech', side: 'L', weight: 2.20, mkt: 2745000, pnl: 3.1, avg: 412.50, spark: [] },
    { ticker: 'NVDA', name: 'NVIDIA Corp.', sector: 'Info Tech', side: 'L', weight: 3.10, mkt: 3870000, pnl: 12.4, avg: 92.30, spark: [] },
    { ticker: 'UNH', name: 'UnitedHealth Group', sector: 'Health Care', side: 'S', weight: 1.40, mkt: 1748000, pnl: -2.6, avg: 488.10, spark: [] },
    { ticker: 'GOOGL', name: 'Alphabet Inc.', sector: 'Comm Svc', side: 'L', weight: 1.90, mkt: 2370000, pnl: 1.4, avg: 168.20, spark: [] },
    { ticker: 'TSLA', name: 'Tesla Inc.', sector: 'Discretionary', side: 'S', weight: 1.80, mkt: 2245000, pnl: 6.1, avg: 312.40, spark: [] },
    { ticker: 'JPM', name: 'JPMorgan Chase', sector: 'Financials', side: 'L', weight: 1.60, mkt: 1995000, pnl: 2.0, avg: 198.50, spark: [] },
    { ticker: 'META', name: 'Meta Platforms', sector: 'Comm Svc', side: 'L', weight: 1.30, mkt: 1623000, pnl: 5.6, avg: 520.10, spark: [] },
    { ticker: 'XOM', name: 'Exxon Mobil', sector: 'Energy', side: 'S', weight: 1.10, mkt: 1372000, pnl: -1.2, avg: 118.30, spark: [] },
    { ticker: 'BAC', name: 'Bank of America', sector: 'Financials', side: 'L', weight: 1.00, mkt: 1247000, pnl: 0.8, avg: 42.10, spark: [] },
  ];
  sectors = [
    { name: 'Info Tech', col: 'var(--c2)', pct: 18.4 },
    { name: 'Comm Svc', col: 'var(--c1)', pct: 12.1 },
    { name: 'Health', col: 'var(--c6)', pct: 10.2 },
    { name: 'Financials', col: 'var(--c8)', pct: 8.4 },
    { name: 'Info Tech (S)', col: 'var(--acc-short)', pct: -14.0 },
    { name: 'Energy (S)', col: 'var(--c4)', pct: -12.0 },
    { name: 'Health (S)', col: 'var(--c7)', pct: -10.0 },
    { name: 'Discretionary (S)', col: 'var(--c3)', pct: -8.0 },
  ];
  feed = [
    { id: '2151', mono: 'RU', col: 'c1', tickers: 'NVDA', sigLabel: 'RUNNING', sig: 'run', conf: '64%', age: '12s', running: true, cost: '' },
    { id: '2150', mono: 'RU', col: 'c5', tickers: 'AMD · INTC', sigLabel: 'RUNNING', sig: 'run', conf: '38%', age: '34s', running: true, cost: '' },
    { id: '2149', mono: 'B', col: 'c2', tickers: 'AAPL', sigLabel: 'BUY', sig: 'buy', conf: '78', age: '4m', running: false, cost: '4.21' },
    { id: '2148', mono: 'H', col: 'c3', tickers: 'GOOGL', sigLabel: 'HOLD', sig: 'hold', conf: '52', age: '11m', running: false, cost: '3.86' },
    { id: '2147', mono: 'S', col: 'c4', tickers: 'TSLA', sigLabel: 'SELL', sig: 'sell', conf: '68', age: '24m', running: false, cost: '4.04' },
    { id: '2146', mono: 'B', col: 'c6', tickers: 'JPM', sigLabel: 'BUY', sig: 'buy', conf: '71', age: '38m', running: false, cost: '3.92' },
  ];

  ngOnInit(): void {
    this.runs.listRuns().subscribe();
    this.macro.loadSnapshot().subscribe({ error: () => {} });
  }
}
