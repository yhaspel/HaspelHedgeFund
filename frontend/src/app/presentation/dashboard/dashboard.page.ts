import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { AuthStore } from '../../abstraction/auth.store';
import { MacroStore } from '../../abstraction/macro.store';
import { RunsStore } from '../../abstraction/runs.store';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { KpiTileComponent } from '../shared/kpi-tile.component';
import { GrossNetMeterComponent } from '../shared/gross-net-meter.component';

type PillKind = 'ok' | 'warn' | 'err' | 'info' | '';

@Component({
  selector: 'hf-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    AppShellComponent,
    KpiTileComponent,
    GrossNetMeterComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{label:'Dashboard'}]">
      <div class="page-head">
        <div><h1>Dashboard</h1></div>
        <div class="head-actions">
          <a class="btn" routerLink="/backtests/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New backtest
          </a>
          <a class="btn primary" routerLink="/runs/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New analysis
          </a>
        </div>
      </div>

      <!-- KPI row: macro + 3 tiles -->
      <div class="kpi-row">
        <!-- Macro tile (wider) -->
        <section class="card macro">
          <div class="card-hd">
            <span class="title">Macro regime</span>
            @if (macro.snapshot(); as s) {
              <span class="pill"><span class="dot"></span>as of {{ s.as_of_date }}</span>
            }
          </div>
          <div class="card-bd">
            @if (macro.snapshot(); as s) {
              <div class="chips">
                <span class="pill" [class.ok]="chipKind('growth', s.growth_quadrant)==='ok'"
                  [class.warn]="chipKind('growth', s.growth_quadrant)==='warn'"
                  [class.err]="chipKind('growth', s.growth_quadrant)==='err'">
                  <span class="dot"></span>growth · {{ s.growth_quadrant }}
                </span>
                <span class="pill" [class.ok]="chipKind('inflation', s.inflation_regime)==='ok'"
                  [class.warn]="chipKind('inflation', s.inflation_regime)==='warn'"
                  [class.err]="chipKind('inflation', s.inflation_regime)==='err'">
                  <span class="dot"></span>inflation · {{ s.inflation_regime }}
                </span>
                <span class="pill" [class.ok]="chipKind('curve', s.yield_curve_state)==='ok'"
                  [class.warn]="chipKind('curve', s.yield_curve_state)==='warn'"
                  [class.err]="chipKind('curve', s.yield_curve_state)==='err'">
                  <span class="dot"></span>curve · {{ s.yield_curve_state }}
                </span>
                <span class="pill" [class.ok]="chipKind('policy', s.policy_stance)==='ok'"
                  [class.warn]="chipKind('policy', s.policy_stance)==='warn'"
                  [class.info]="chipKind('policy', s.policy_stance)==='info'">
                  <span class="dot"></span>policy · {{ s.policy_stance }}
                </span>
              </div>
              <p class="narrative">{{ s.narrative }}</p>
            } @else {
              <p class="muted">Loading macro snapshot…</p>
            }
          </div>
        </section>

        @if (book(); as b) {
          <hf-kpi-tile
            eyebrow="Gross"
            [value]="b.gross_pct + '%'"
            [sub]="'Long ' + b.longPct.toFixed(0) + '% · Short ' + b.shortPct.toFixed(0) + '%'" />
          <hf-kpi-tile
            eyebrow="Net"
            [value]="(b.netSigned >= 0 ? '+' : '') + b.netSigned.toFixed(1) + '%'"
            [tone]="b.netSigned >= 0 ? 'up' : 'down'"
            [sub]="b.strategy_name + ' · ' + b.as_of_date" />
          <hf-kpi-tile
            eyebrow="Positions"
            [value]="b.positionCount.toString()"
            [sub]="b.longCount + ' L · ' + b.shortCount + ' S'" />
        } @else {
          <section class="card placeholder">
            <p class="muted">No portfolio yet.
              <a routerLink="/strategies/new" class="link">Create a strategy →</a>
            </p>
          </section>
        }
      </div>

      <!-- Gross·Net meter (only when we have data) -->
      @if (book(); as b) {
        <section class="card" style="margin-bottom:18px">
          <div class="card-hd">
            <span class="title">Exposure</span>
            <span class="pill"><span class="dot"></span>target gross {{ b.target_gross_pct }}% · net {{ b.target_net_pct }}%</span>
          </div>
          <div class="card-bd">
            <hf-gross-net-meter [longPct]="b.longPct" [shortPct]="b.shortPct" />
            <div class="meter-labels">
              <span class="long-lbl">Long {{ b.longPct.toFixed(1) }}%</span>
              <span class="short-lbl">Short {{ b.shortPct.toFixed(1) }}%</span>
            </div>
          </div>
        </section>
      }

      <!-- 2-col: Active runs + Strategies summary -->
      <div class="two-col">
        <section class="card">
          <div class="card-hd">
            <span class="title">Active runs</span>
            <span class="pill"
              [class.warn]="activeRuns().length > 0"
              [class.ok]="activeRuns().length === 0">
              <span class="dot"></span>{{ activeRuns().length }} in flight
            </span>
            <!-- P2l source filter -->
            <div class="actions" style="gap:4px;align-items:center">
              <button class="chip" [class.chip-on]="runSource() === 'all'"
                      (click)="setRunSource('all')" data-test="runs-filter-all">All</button>
              <button class="chip" [class.chip-on]="runSource() === 'adhoc'"
                      (click)="setRunSource('adhoc')" data-test="runs-filter-adhoc">Manual</button>
              <button class="chip" [class.chip-on]="runSource() === 'strategy'"
                      (click)="setRunSource('strategy')" data-test="runs-filter-strategy">Strategy</button>
            </div>
          </div>
          <div class="card-bd">
            @if (activeRuns().length === 0) {
              <p class="muted">No runs in flight.</p>
            } @else {
              <ul class="runlist">
                @for (r of activeRuns(); track r.id) {
                  <li>
                    <a [routerLink]="['/runs', r.id]" class="runrow">
                      <span class="run-id mono">#{{ r.id }}</span>
                      <span class="run-tickers mono">{{ r.tickers.join(', ') }}</span>
                      @if (r.source === 'strategy' && r.strategy_backlink) {
                        <span class="pill" style="background:var(--surface-2);color:var(--text-3);height:auto;padding:2px 6px;font-size:11px">
                          via {{ r.strategy_backlink.strategy_name }}
                        </span>
                      }
                      <span class="pill warn"><span class="dot"></span>{{ r.status }}</span>
                    </a>
                  </li>
                }
              </ul>
            }
            @if (recentDone().length > 0) {
              <div class="eyebrow recent-eyebrow">Recent</div>
              <ul class="runlist">
                @for (r of recentDone(); track r.id) {
                  <li>
                    <a [routerLink]="['/runs', r.id]" class="runrow">
                      <span class="run-id mono">#{{ r.id }}</span>
                      <span class="run-tickers mono">{{ r.tickers.join(', ') }}</span>
                      @if (r.source === 'strategy' && r.strategy_backlink) {
                        <span class="pill" style="background:var(--surface-2);color:var(--text-3);height:auto;padding:2px 6px;font-size:11px">
                          via {{ r.strategy_backlink.strategy_name }}
                        </span>
                      }
                      <span class="pill"
                        [class.ok]="r.status==='done'"
                        [class.err]="r.status==='failed' || r.status==='cancelled'">
                        <span class="dot"></span>{{ r.status }}
                      </span>
                      <span class="cost mono">$ {{ (+r.total_cost_usd).toFixed(4) }}</span>
                    </a>
                  </li>
                }
              </ul>
            }
          </div>
        </section>

        <section class="card">
          <div class="card-hd">
            <span class="title">Strategies</span>
            <a routerLink="/strategies" class="link mono">View all →</a>
          </div>
          @if (strategies.strategies().length === 0) {
            <div class="card-bd">
              <p class="muted">No strategies yet.
                <a routerLink="/strategies/new" class="link">Create one →</a>
              </p>
            </div>
          } @else {
            <table class="tbl">
              <thead><tr>
                <th>Name</th><th>Kind</th><th>Universe</th>
                <th>Status</th><th>Last cycle</th>
              </tr></thead>
              <tbody>
                @for (s of topStrategies(); track s.id) {
                  <tr>
                    <td><a [routerLink]="['/strategies', s.id]" class="link">{{ s.name }}</a></td>
                    <td><span class="pill mono">{{ s.kind }}</span></td>
                    <td class="mono" style="color:var(--text-2)">{{ s.universe_name }}</td>
                    <td>
                      <span class="pill"
                        [class.ok]="s.is_active"
                        [class.warn]="!s.is_active">
                        <span class="dot"></span>{{ s.is_active ? 'active' : 'paused' }}
                      </span>
                    </td>
                    <td class="mono" style="color:var(--text-3)">{{ s.last_run_at ? (s.last_run_at | date:'yyyy-MM-dd') : '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .kpi-row {
        display: grid;
        grid-template-columns: 2fr 1fr 1fr 1fr;
        gap: 12px;
        margin-bottom: 18px;
      }
      @media (max-width: 1100px) {
        .kpi-row { grid-template-columns: 1fr 1fr; }
        .kpi-row .macro { grid-column: 1 / -1; }
      }
      .kpi-row .macro { margin: 0; }
      .kpi-row .placeholder {
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 14px;
        grid-column: span 3;
      }
      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 10px;
      }
      .narrative {
        font-size: 13px;
        color: var(--text-2);
        line-height: 20px;
        margin: 0;
      }
      .muted { font-size: 13px; color: var(--text-3); margin: 0; }
      .link { color: var(--acc-info-fg); text-decoration: none; }
      .link:hover { text-decoration: underline; }
      .meter-labels {
        display: flex;
        justify-content: space-between;
        font-family: var(--font-mono);
        font-size: 11px;
        margin-top: 6px;
      }
      .long-lbl { color: var(--acc-long-fg); }
      .short-lbl { color: var(--acc-short-fg); }

      .two-col {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }
      @media (max-width: 1100px) {
        .two-col { grid-template-columns: 1fr; }
      }
      .runlist {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .runrow {
        display: grid;
        grid-template-columns: 56px 1fr auto auto;
        align-items: center;
        gap: 10px;
        padding: 8px 10px;
        border-radius: var(--r-6, 6px);
        text-decoration: none;
        color: var(--text);
        transition: background var(--dur-fast, 120ms) var(--ease-out-ui);
      }
      .runrow:hover { background: var(--surface-2); }
      .run-id { color: var(--text-3); font-size: 12px; }
      .run-tickers {
        color: var(--text);
        font-size: 13px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .cost { color: var(--text-2); font-size: 12px; }
      .recent-eyebrow {
        margin-top: 14px;
        margin-bottom: 6px;
        color: var(--text-3);
        font-size: 10px;
      }
      .chip {
        background: transparent;
        border: 1px solid var(--border);
        color: var(--text-2);
        font-size: 11px;
        line-height: 1;
        padding: 4px 8px;
        border-radius: 999px;
        cursor: pointer;
      }
      .chip:hover { background: var(--surface-2); }
      .chip-on {
        background: var(--surface-2);
        color: var(--text);
        border-color: var(--text-3);
      }
    `,
  ],
})
export class DashboardPage implements OnInit {
  readonly auth = inject(AuthStore);
  readonly runs = inject(RunsStore);
  readonly macro = inject(MacroStore);
  readonly strategies = inject(StrategiesStore);

  book = signal<{
    strategy_name: string;
    as_of_date: string;
    gross_pct: string;
    net_pct: string;
    target_gross_pct: string;
    target_net_pct: string;
    longPct: number;
    shortPct: number;
    netSigned: number;
    longCount: number;
    shortCount: number;
    positionCount: number;
    longs: { ticker: string; weight: number }[];
    shorts: { ticker: string; weight: number }[];
  } | null>(null);

  // P2l: source filter for the runs list. 'all' shows both.
  runSource = signal<'all' | 'adhoc' | 'strategy'>('all');

  activeRuns = computed(() =>
    this.runs.runs().filter((r) => r.status === 'running' || r.status === 'queued'),
  );
  recentDone = computed(() =>
    this.runs.runs()
      .filter((r) => r.status !== 'running' && r.status !== 'queued')
      .slice(0, 5),
  );
  topStrategies = computed(() => this.strategies.strategies().slice(0, 5));

  setRunSource(s: 'all' | 'adhoc' | 'strategy'): void {
    this.runSource.set(s);
    this.runs.listRuns({ source: s }).subscribe();
  }

  ngOnInit(): void {
    this.runs.listRuns().subscribe();
    this.macro.loadSnapshot().subscribe({ error: () => {} });
    this.strategies.list().subscribe((ss) => {
      const recent = ss.find((s) => !!s.last_run_at) ?? ss[0];
      if (!recent) return;
      this.strategies.listCycles(recent.id).subscribe((cs) => {
        const done = cs.find((c) => c.status === 'done') ?? cs[0];
        if (!done) return;
        this.strategies.cycleDetail(recent.id, done.id).subscribe((d) => {
          const allLongs = Object.entries(d.target_weights)
            .filter(([, w]) => Number(w) > 0)
            .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }));
          const allShorts = Object.entries(d.target_weights)
            .filter(([, w]) => Number(w) < 0)
            .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }));
          const longPct = allLongs.reduce((a, b) => a + b.weight, 0);
          const shortPct = Math.abs(allShorts.reduce((a, b) => a + b.weight, 0));
          this.book.set({
            strategy_name: recent.name,
            as_of_date: d.as_of_date,
            gross_pct: d.gross_pct,
            net_pct: d.net_pct,
            target_gross_pct: recent.target_gross_pct,
            target_net_pct: recent.target_net_pct,
            longPct,
            shortPct,
            netSigned: longPct - shortPct,
            longCount: allLongs.length,
            shortCount: allShorts.length,
            positionCount: allLongs.length + allShorts.length,
            longs: allLongs.sort((a, b) => b.weight - a.weight).slice(0, 5),
            shorts: allShorts.sort((a, b) => a.weight - b.weight).slice(0, 5),
          });
        });
      });
    });
  }

  chipKind(kind: string, value: string): PillKind {
    const map: Record<string, Record<string, PillKind>> = {
      growth: { expansion: 'ok', recovery: 'ok', slowdown: 'warn', recession: 'err' },
      inflation: { low: 'ok', moderate: '', high: 'warn', accelerating: 'err' },
      curve: { normal: 'ok', flat: 'warn', inverted: 'err' },
      policy: { easing: 'info', neutral: '', tightening: 'warn' },
    };
    return map[kind]?.[value] ?? '';
  }
}
