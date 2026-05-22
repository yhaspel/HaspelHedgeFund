import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { AuthStore } from '../../abstraction/auth.store';
import { MacroStore } from '../../abstraction/macro.store';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import { RunsStore } from '../../abstraction/runs.store';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { KpiTileComponent } from '../shared/kpi-tile.component';
import { GrossNetMeterComponent } from '../shared/gross-net-meter.component';
import { TickerComponent } from '../shared/ticker.component';

type PillKind = 'ok' | 'warn' | 'err' | 'info' | '';

@Component({
  selector: 'hf-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    DecimalPipe,
    RouterLink,
    AppShellComponent,
    KpiTileComponent,
    GrossNetMeterComponent,
    TickerComponent,
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
                <span class="pill help" [class.ok]="chipKind('growth', s.growth_quadrant)==='ok'"
                  [class.warn]="chipKind('growth', s.growth_quadrant)==='warn'"
                  [class.err]="chipKind('growth', s.growth_quadrant)==='err'"
                  (mouseenter)="positionTip($event)" (focus)="positionTip($event)" tabindex="0">
                  <span class="dot"></span>growth · {{ s.growth_quadrant }}
                  <span class="tip">{{ chipTooltip('growth', s.growth_quadrant) }}</span>
                </span>
                <span class="pill help" [class.ok]="chipKind('inflation', s.inflation_regime)==='ok'"
                  [class.warn]="chipKind('inflation', s.inflation_regime)==='warn'"
                  [class.err]="chipKind('inflation', s.inflation_regime)==='err'"
                  (mouseenter)="positionTip($event)" (focus)="positionTip($event)" tabindex="0">
                  <span class="dot"></span>inflation · {{ s.inflation_regime }}
                  <span class="tip">{{ chipTooltip('inflation', s.inflation_regime) }}</span>
                </span>
                <span class="pill help" [class.ok]="chipKind('curve', s.yield_curve_state)==='ok'"
                  [class.warn]="chipKind('curve', s.yield_curve_state)==='warn'"
                  [class.err]="chipKind('curve', s.yield_curve_state)==='err'"
                  (mouseenter)="positionTip($event)" (focus)="positionTip($event)" tabindex="0">
                  <span class="dot"></span>curve · {{ s.yield_curve_state }}
                  <span class="tip">{{ chipTooltip('curve', s.yield_curve_state) }}</span>
                </span>
                <span class="pill help" [class.ok]="chipKind('policy', s.policy_stance)==='ok'"
                  [class.warn]="chipKind('policy', s.policy_stance)==='warn'"
                  [class.info]="chipKind('policy', s.policy_stance)==='info'"
                  (mouseenter)="positionTip($event)" (focus)="positionTip($event)" tabindex="0">
                  <span class="dot"></span>policy · {{ s.policy_stance }}
                  <span class="tip">{{ chipTooltip('policy', s.policy_stance) }}</span>
                </span>
                @if (s.markov_consensus; as mc) {
                  @if (mc.consensus_state !== 'unavailable') {
                    <span class="pill help"
                          [class.ok]="mc.consensus_state==='bull'"
                          [class.warn]="mc.consensus_state==='sideways'"
                          [class.err]="mc.consensus_state==='bear'"
                          (mouseenter)="positionTip($event)" (focus)="positionTip($event)" tabindex="0">
                      <span class="dot"></span>markov · {{ mc.consensus_state }} ({{ (mc.consensus_strength*100).toFixed(0) }}%)
                      <span class="tip">{{ markovTooltip(mc) }}</span>
                    </span>
                  }
                }
              </div>
              <p class="narrative">{{ s.narrative }}</p>
              @if (s.markov_consensus; as mc) {
                @if (mc.available_count > 0) {
                  <p class="muted" style="font-size:11.5px;margin-top:4px">
                    Markov consensus: {{ mc.vote.bull || 0 }} bull · {{ mc.vote.sideways || 0 }} sideways · {{ mc.vote.bear || 0 }} bear
                    across {{ mc.available_count }} always-modelled ETFs
                    @if (mc.stale_count > 0) { · {{ mc.stale_count }} stale }
                  </p>
                }
              }
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
            eyebrow="Target book (latest cycle)"
            [value]="b.positionCount.toString()"
            [sub]="b.longCount + ' L · ' + b.shortCount + ' S · ' + b.as_of_date" />
        } @else {
          <section class="card placeholder">
            <p class="muted">No strategy cycles yet.
              <a routerLink="/strategies/new" class="link">Create a strategy →</a>
            </p>
          </section>
          <section class="card placeholder">
            <p class="muted">—</p>
          </section>
        }

        <!-- P3: Real Manual Book positions, not strategy target weights. -->
        @if (portfolio.overview(); as p) {
          <hf-kpi-tile
            eyebrow="Positions (Manual Book)"
            [value]="p.positions.length.toString()"
            [sub]="manualLongCount() + ' L · ' + manualShortCount() + ' S · $' + (+p.total_value | number: '1.0-0')" />
        } @else {
          <a class="card placeholder kpi-link"
             routerLink="/portfolio" style="text-decoration:none;color:inherit">
            <p class="muted">Loading Manual Book…</p>
          </a>
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
            <div class="actions" role="radiogroup" aria-label="Filter runs by source"
                 style="gap:4px;align-items:center"
                 (keydown)="onRunSourceKeydown($event)">
              <button class="chip" type="button" role="radio"
                      [class.chip-on]="runSource() === 'all'"
                      [attr.aria-checked]="runSource() === 'all'"
                      [attr.tabindex]="runSource() === 'all' ? 0 : -1"
                      (click)="setRunSource('all')" data-test="runs-filter-all">All</button>
              <button class="chip" type="button" role="radio"
                      [class.chip-on]="runSource() === 'adhoc'"
                      [attr.aria-checked]="runSource() === 'adhoc'"
                      [attr.tabindex]="runSource() === 'adhoc' ? 0 : -1"
                      (click)="setRunSource('adhoc')" data-test="runs-filter-adhoc">Manual</button>
              <button class="chip" type="button" role="radio"
                      [class.chip-on]="runSource() === 'strategy'"
                      [attr.aria-checked]="runSource() === 'strategy'"
                      [attr.tabindex]="runSource() === 'strategy' ? 0 : -1"
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
                    <div class="runrow" role="link" tabindex="0"
                         [attr.aria-label]="'Open run ' + r.id"
                         (click)="openRun(r.id)"
                         (keydown.enter)="openRun(r.id)"
                         (keydown.space)="openRun(r.id); $event.preventDefault()">
                      <span class="run-id mono">#{{ r.id }}</span>
                      <span class="run-tickers">
                        @for (t of r.tickers; track t; let last = $last) {
                          <hf-ticker [ticker]="t"></hf-ticker>@if (!last) {<span style="color:var(--text-3)">, </span>}
                        }
                      </span>
                      @if (r.source === 'strategy' && r.strategy_backlink) {
                        <span class="pill" style="background:var(--surface-2);color:var(--text-3);height:auto;padding:2px 6px;font-size:11px">
                          via {{ r.strategy_backlink.strategy_name }}
                        </span>
                      }
                      <span class="pill warn"><span class="dot"></span>{{ r.status }}</span>
                    </div>
                  </li>
                }
              </ul>
            }
            @if (recentDone().length > 0) {
              <div class="eyebrow recent-eyebrow">Recent</div>
              <ul class="runlist">
                @for (r of recentDone(); track r.id) {
                  <li>
                    <div class="runrow" role="link" tabindex="0"
                         [attr.aria-label]="'Open run ' + r.id"
                         (click)="openRun(r.id)"
                         (keydown.enter)="openRun(r.id)"
                         (keydown.space)="openRun(r.id); $event.preventDefault()">
                      <span class="run-id mono">#{{ r.id }}</span>
                      <span class="run-tickers">
                        @for (t of r.tickers; track t; let last = $last) {
                          <hf-ticker [ticker]="t"></hf-ticker>@if (!last) {<span style="color:var(--text-3)">, </span>}
                        }
                      </span>
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
                    </div>
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
        position: relative;
      }
      .chips .pill.help {
        cursor: help;
        text-decoration: underline dotted;
        text-decoration-color: var(--text-3, rgba(255, 255, 255, 0.25));
        text-underline-offset: 3px;
        position: relative;
      }
      .chips .pill.help .tip {
        position: absolute;
        bottom: calc(100% + 8px);
        left: 0;
        z-index: 50;
        width: 320px;
        max-width: 90vw;
        padding: 10px 12px;
        background: var(--surface-2, #161a21);
        color: var(--text, #f6f8fb);
        border: 1px solid var(--border-2, #2c313d);
        border-radius: 6px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
        font-size: 12px;
        font-weight: 400;
        line-height: 1.55;
        text-align: left;
        text-decoration: none;
        white-space: pre-line;
        letter-spacing: normal;
        opacity: 0;
        visibility: hidden;
        transform: translateY(2px);
        transition: opacity 120ms ease, transform 120ms ease, visibility 120ms;
        pointer-events: none;
      }
      .chips .pill.help:hover .tip,
      .chips .pill.help:focus-visible .tip {
        opacity: 1;
        visibility: visible;
        transform: translateY(0);
      }
      /* Viewport-aware: flip the tooltip below the chip when there's no
         room above. positionTip() toggles these classes on mouseenter. */
      .chips .pill.help.tip-below .tip {
        bottom: auto;
        top: calc(100% + 8px);
        transform: translateY(-2px);
      }
      .chips .pill.help.tip-below:hover .tip,
      .chips .pill.help.tip-below:focus-visible .tip {
        transform: translateY(0);
      }
      .chips .pill.help.tip-right-aligned .tip {
        left: auto;
        right: 0;
      }
      .card.macro { overflow: visible; }
      .narrative {
        font-size: 13px;
        color: var(--text-2);
        line-height: 20px;
        margin: 0;
      }
      .muted { font-size: 13px; color: var(--text-3); margin: 0; }
      .link { color: var(--acc-info-fg); text-decoration: underline; text-underline-offset: 2px; }
      .link:hover { text-decoration-thickness: 2px; }
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
        cursor: pointer;
        transition: background var(--dur-fast, 120ms) var(--ease-out-ui);
      }
      .runrow:hover { background: var(--surface-2); }
      .runrow:focus-visible { outline: none; box-shadow: var(--focus-ring); }
      .run-tickers {
        display: inline-flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 4px;
        font-family: var(--font-mono);
        font-size: 13px;
        color: var(--text);
      }
      .run-id { color: var(--text-3); font-size: 12px; }
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
        padding: 0 10px;
        min-height: 24px;
        display: inline-flex;
        align-items: center;
        border-radius: var(--r-full);
        cursor: pointer;
      }
      .chip:hover { background: var(--surface-2); }
      .chip:focus-visible { outline: none; box-shadow: var(--focus-ring); }
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
  readonly portfolio = inject(PortfolioStore);
  private readonly profiles = inject(TickerProfileStore);
  private readonly router = inject(Router);

  openRun(id: number): void { this.router.navigate(['/runs', id]); }

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
    this.runs.listRuns({ source: s }).subscribe((rows) => this._prefetchTickerNames(rows));
  }

  /** WS-3.5: roving-tabindex arrow-key navigation for the source filter radiogroup. */
  onRunSourceKeydown(ev: KeyboardEvent): void {
    const order: ('all' | 'adhoc' | 'strategy')[] = ['all', 'adhoc', 'strategy'];
    const current = order.indexOf(this.runSource());
    let next = current;
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') next = (current + 1) % order.length;
    else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') next = (current - 1 + order.length) % order.length;
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = order.length - 1;
    else return;
    ev.preventDefault();
    this.setRunSource(order[next]);
    const group = ev.currentTarget as HTMLElement;
    const buttons = group.querySelectorAll<HTMLButtonElement>('button[role="radio"]');
    buttons[next]?.focus();
  }

  private _prefetchTickerNames(rows: { tickers: string[] }[]): void {
    const tickers = [...new Set(rows.flatMap((r) => r.tickers || []))];
    if (tickers.length) this.profiles.fetchNames(tickers).subscribe();
  }

  manualLongCount(): number {
    return (this.portfolio.overview()?.positions ?? []).filter((p) => !p.is_short).length;
  }

  manualShortCount(): number {
    return (this.portfolio.overview()?.positions ?? []).filter((p) => p.is_short).length;
  }

  ngOnInit(): void {
    this.runs.listRuns().subscribe((rows) => this._prefetchTickerNames(rows));
    this.macro.loadSnapshot().subscribe({ error: () => {} });
    // P3: load the Manual Book so the Positions KPI reflects the real book,
    // not the latest strategy cycle's target-weight count.
    this.portfolio.loadOverview().subscribe({ error: () => {} });
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

  /**
   * Hover tooltip for each macro chip. Format: header explaining what the
   * metric is, then a line decoding the current value. Values + thresholds
   * mirror `hedgefund_agents.macro.macro_agent.classify_regime`.
   */
  chipTooltip(kind: string, value: string): string {
    const HEADERS: Record<string, string> = {
      growth:
        'Growth quadrant — derived from unemployment (UNRATE) and industrial production (INDPRO).',
      inflation: 'Inflation regime — based on CPI level (CPIAUCSL).',
      curve: 'Yield curve — 10-year minus 2-year Treasury spread (T10Y2Y).',
      policy: 'Policy stance — Federal Funds effective rate (FEDFUNDS).',
    };
    const VALUES: Record<string, Record<string, string>> = {
      growth: {
        expansion: 'Expansion: unemployment ≤4% and INDPRO ≥100. Healthy growth — risk-on tilt favoured.',
        recovery: 'Recovery: improving labour market, INDPRO climbing back. Early-cycle conditions.',
        slowdown: 'Slowdown: unemployment ≥4.5%. Late-cycle deceleration — trim cyclicals, watch credit.',
        recession: 'Recession: unemployment ≥5.5% and INDPRO ≤100. Defensive tilt; growth contracting.',
      },
      inflation: {
        low: 'Low: CPI <290. Disinflationary backdrop — supports long duration / growth equities.',
        moderate: 'Moderate: CPI 290–320. Mid-range price pressures — broad-market neutral.',
        high: 'High: CPI ≥320. Persistent inflation — favours commodities, value, short duration.',
        accelerating: 'Accelerating: inflation rising fast — defensive tilt, reduce long duration.',
      },
      curve: {
        normal: 'Normal: 10y−2y ≥0.5pp. Healthy term premium; no recession signal from the curve.',
        flat: 'Flat: 10y−2y in (0, 0.5pp). Late-cycle warning — curve is compressing.',
        inverted: 'Inverted: 10y−2y <0. Historically a recession leading indicator (12–18mo lag).',
      },
      policy: {
        easing: 'Easing: Fed Funds ≤2%. Stimulative monetary policy — supports risk assets and duration.',
        neutral: 'Neutral: Fed Funds 2–4.5%. Neither restrictive nor stimulative.',
        tightening: 'Tightening: Fed Funds ≥4.5%. Restrictive policy — headwind for duration and growth.',
      },
    };
    const header = HEADERS[kind] ?? '';
    const explanation = VALUES[kind]?.[value] ?? `Current value: ${value}.`;
    return header ? `${header}\n\n${explanation}` : explanation;
  }

  /**
   * Viewport-aware tooltip positioning. Called on hover/focus of a chip.
   * Measures the chip's bounding rect against the tooltip's natural size
   * and toggles `.tip-below` / `.tip-right-aligned` classes so the tip
   * never gets clipped by the top or right edge of the viewport.
   */
  positionTip(event: Event): void {
    const chip = event.currentTarget as HTMLElement | null;
    if (!chip) return;
    const tip = chip.querySelector('.tip') as HTMLElement | null;
    if (!tip) return;
    // Temporarily reveal off-screen to measure (display:block makes
    // getBoundingClientRect honest while opacity/visibility still hide it).
    const prevVis = tip.style.visibility;
    const prevDisp = tip.style.display;
    tip.style.visibility = 'hidden';
    tip.style.display = 'block';
    const tipRect = tip.getBoundingClientRect();
    tip.style.display = prevDisp;
    tip.style.visibility = prevVis;

    const chipRect = chip.getBoundingClientRect();
    const margin = 12;
    const flipBelow = chipRect.top - tipRect.height - margin < 0;
    chip.classList.toggle('tip-below', flipBelow);

    const overflowRight = chipRect.left + tipRect.width + margin > window.innerWidth;
    chip.classList.toggle('tip-right-aligned', overflowRight);
  }

  /** Hover tooltip for the Markov consensus chip. */
  markovTooltip(mc: {
    consensus_state: string;
    consensus_strength: number;
    vote: Record<string, number>;
    available_count: number;
    stale_count: number;
  }): string {
    const header =
      'Markov regime consensus — deterministic, price-based regime detector ' +
      'run nightly on SPY, QQQ, the 11 SPDR sector ETFs, and TLT/GLD/UUP.';
    const winner = mc.consensus_state;
    const strength = (mc.consensus_strength * 100).toFixed(0);
    const tally =
      `${mc.vote['bull'] ?? 0} bull · ${mc.vote['sideways'] ?? 0} sideways · ` +
      `${mc.vote['bear'] ?? 0} bear (out of ${mc.available_count} fresh snapshots` +
      (mc.stale_count > 0 ? `, ${mc.stale_count} stale` : '') +
      ').';
    return (
      `${header}\n\nCurrent: ${winner} at ${strength}% agreement.\n${tally}\n\n` +
      'Disagreement with the LLM macro classification is itself a signal — see the strategy detail page widget.'
    );
  }
}
