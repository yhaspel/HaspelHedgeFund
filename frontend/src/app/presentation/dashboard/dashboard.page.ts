import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { AuthStore } from '../../abstraction/auth.store';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { FundStore } from '../../abstraction/fund.store';
import { MacroStore } from '../../abstraction/macro.store';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import { RunsStore } from '../../abstraction/runs.store';
import { strategyKindLabel } from '../../core/models/strategy.model';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { RunSummary } from '../../core/models/run.model';
import { KpiTileComponent } from '../shared/kpi-tile.component';
import { TickerComponent } from '../shared/ticker.component';
import { WatchlistCardComponent } from '../watchlist/watchlist-card.component';
import { NavHeroComponent } from './nav-hero.component';
import { BookExposureComponent, BookExposureVm } from './book-exposure.component';
import { RegimeStripComponent } from './regime-strip.component';
import { SectorHeatmapComponent } from './sector-heatmap.component';

type SigKind = 'buy' | 'sell' | 'hold' | 'info';

@Component({
  selector: 'hf-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    AppShellComponent,
    EmptyStateComponent,
    KpiTileComponent,
    TickerComponent,
    WatchlistCardComponent,
    NavHeroComponent,
    BookExposureComponent,
    RegimeStripComponent,
    SectorHeatmapComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{label:'Manual dashboard'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Manual book &amp; research desk</div>
          <h1 class="mt-1.5">Manual dashboard</h1>
        </div>
        <div class="head-actions">
          <a class="btn" routerLink="/backtests/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New backtest
          </a>
          <a class="btn primary" routerLink="/runs/new">
            <svg width="12" height="12"><use href="/icons.svg#i-plus" /></svg> New analysis
          </a>
        </div>
      </div>

      <!-- KPI strip: 5 Manual-Book tiles (all from /portfolio/) -->
      <div class="kpi-strip">
        @if (portfolio.overview(); as p) {
          <hf-kpi-tile
            eyebrow="NAV · Manual Book"
            [value]="compact(p.total_value)"
            [sub]="p.positions.length + ' positions'" />
          <hf-kpi-tile
            eyebrow="Unrealized P&amp;L"
            [value]="signed(p.unrealized_pnl)"
            [tone]="signTone(p.unrealized_pnl)"
            [sub]="'realized ' + signed(p.realized_pnl)" />
          <hf-kpi-tile
            eyebrow="Gross exposure"
            [value]="pct(p.gross_exposure_pct)"
            [sub]="money(p.long_market_value) + ' L · ' + money(p.short_market_value) + ' S'" />
          <hf-kpi-tile
            eyebrow="Net exposure"
            [value]="netStr(p.net_exposure_pct)"
            [tone]="signTone(p.net_exposure_pct)"
            [sub]="netBias(p.net_exposure_pct)" />
          <hf-kpi-tile
            eyebrow="Open positions"
            [value]="p.positions.length.toString()"
            [sub]="manualLongCount() + ' L · ' + manualShortCount() + ' S'" />
        } @else {
          @for (_ of [1,2,3,4,5]; track $index) {
            <section class="card placeholder" aria-busy="true" aria-label="Loading Manual Book">
              <div class="skel h-2.5 w-[55%]"></div>
              <div class="skel h-[26px] w-[70%] mt-2"></div>
              <div class="skel h-[11px] w-[80%] mt-2"></div>
            </section>
          }
        }
      </div>

      <!-- Hero: NAV (point-in-time, no series) + strategy book exposure -->
      <div class="hero-top">
        <hf-nav-hero [overview]="portfolio.overview()" />
        <hf-book-exposure [book]="book()" [loaded]="bookLoaded()" />
      </div>

      <!-- Compact macro-regime strip (below NAV) -->
      <hf-regime-strip [snapshot]="macro.snapshot()" />

      <!-- Activity: active runs + strategies summary -->
      <div class="two-col">
        <section class="card">
          <div class="card-hd">
            <h2 class="title">Active runs</h2>
            <span class="pill"
              [class.warn]="activeRuns().length > 0"
              [class.ok]="activeRuns().length === 0">
              <span class="dot"></span>{{ activeRuns().length }} in flight
            </span>
            <!-- P2l source filter -->
            <div class="actions gap-1 items-center" role="radiogroup" aria-label="Filter runs by source"
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
            @if (activeRuns().length === 0 && !runsLoaded()) {
              <div aria-busy="true" aria-label="Loading runs" class="flex flex-col gap-2">
                @for (_ of [1,2,3]; track $index) {
                  <div class="skel h-[28px] w-full"></div>
                }
              </div>
            } @else if (activeRuns().length === 0) {
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
                          <hf-ticker [ticker]="t"></hf-ticker>@if (!last) {<span class="text-text-3">, </span>}
                        }
                      </span>
                      @if (r.source === 'strategy' && r.strategy_backlink) {
                        <span class="pill pill-source">
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
                          <hf-ticker [ticker]="t"></hf-ticker>@if (!last) {<span class="text-text-3">, </span>}
                        }
                      </span>
                      @if (runSig(r); as sig) {
                        <span class="sig"
                          [class.buy]="sig.kind==='buy'"
                          [class.sell]="sig.kind==='sell'"
                          [class.hold]="sig.kind==='hold'"
                          [class.info]="sig.kind==='info'">{{ sig.label }}</span>
                      }
                      @if (r.source === 'strategy' && r.strategy_backlink) {
                        <span class="pill pill-source">
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
            <h2 class="title">Strategies</h2>
            <a routerLink="/strategies" class="link mono">View all →</a>
          </div>
          @if (strategies.strategies().length === 0 && !strategiesLoaded()) {
            <div class="card-bd flex flex-col gap-2" aria-busy="true" aria-label="Loading strategies">
              @for (_ of [1,2,3]; track $index) {
                <div class="skel h-[28px] w-full"></div>
              }
            </div>
          } @else if (strategies.strategies().length === 0) {
            <hf-empty-state message="No strategies yet.">
              <a class="btn primary" routerLink="/strategies/new">Create a strategy</a>
            </hf-empty-state>
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
                    <td><span class="pill" data-test="dash-strategy-kind">{{ kindLabel(s.kind) }}</span></td>
                    <td class="mono text-text-2">{{ s.universe_name }}</td>
                    <td>
                      <span class="pill"
                        [class.ok]="s.is_active"
                        [class.warn]="!s.is_active">
                        <span class="dot"></span>{{ s.is_active ? 'active' : 'paused' }}
                      </span>
                    </td>
                    <td class="mono text-text-3">{{ s.last_run_at ? (s.last_run_at | date:'yyyy-MM-dd') : '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>
      </div>

      <!-- Sector implications heatmap (from macro.sector_implications) -->
      @if (macro.snapshot(); as s) {
        <hf-sector-heatmap [implications]="s.sector_implications" />
      }

      <!-- P3-prereq-5: Watchlist card (interactive footnote, full-width). -->
      <div class="watchlist-row">
        <hf-watchlist-card></hf-watchlist-card>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .kpi-strip {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 12px;
        margin-bottom: 18px;
      }
      @media (max-width: 1180px) {
        .kpi-strip { grid-template-columns: repeat(2, 1fr); }
      }
      .kpi-strip .placeholder { padding: 14px 16px; min-height: 96px; }

      .hero-top {
        display: grid;
        grid-template-columns: 1.3fr 1fr;
        gap: 12px;
        margin-bottom: 18px;
      }
      @media (max-width: 1180px) {
        .hero-top { grid-template-columns: 1fr; }
      }

      hf-regime-strip { display: block; margin-bottom: 18px; }
      hf-sector-heatmap { display: block; margin-bottom: 18px; }

      .two-col {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        margin-bottom: 18px;
      }
      @media (max-width: 1180px) {
        .two-col { grid-template-columns: 1fr; }
      }

      .muted { font-size: 13px; color: var(--text-3); margin: 0; }
      .link { color: var(--acc-info-fg); text-decoration: underline; text-underline-offset: 2px; }
      .link:hover { text-decoration-thickness: 2px; }

      .runlist {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .runrow {
        display: flex;
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
        flex: 1;
        min-width: 0;
        display: inline-flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 4px;
        font-family: var(--font-mono);
        font-size: 13px;
        color: var(--text);
      }
      .run-id { color: var(--text-3); font-size: 12px; flex: none; }
      .cost { color: var(--text-2); font-size: 12px; flex: none; }
      .recent-eyebrow {
        margin-top: 14px;
        margin-bottom: 6px;
        color: var(--text-3);
        font-size: 10px;
      }
      .sig {
        flex: none;
        font-family: var(--font-mono);
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        padding: 2px 7px;
        border-radius: var(--r-full);
        background: var(--surface-2);
        color: var(--text-2);
      }
      .sig.buy { background: var(--acc-long-soft); color: var(--acc-long-fg); }
      .sig.sell { background: var(--acc-short-soft); color: var(--acc-short-fg); }
      .sig.hold { background: var(--acc-hold-soft); color: var(--acc-hold-fg); }
      .sig.info { background: var(--acc-info-soft); color: var(--acc-info-fg); }
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
      .pill-source {
        flex: none;
        background: var(--surface-2);
        color: var(--text-3);
        height: auto;
        padding: 2px 6px;
        font-size: 11px;
      }
      .watchlist-row { margin-bottom: 0; }
    `,
  ],
})
export class DashboardPage implements OnInit {
  /** WAVE 3 — no raw `sector_momentum` slugs in the strategy table. */
  readonly kindLabel = strategyKindLabel;
  readonly auth = inject(AuthStore);
  readonly runs = inject(RunsStore);
  readonly macro = inject(MacroStore);
  readonly strategies = inject(StrategiesStore);
  readonly portfolio = inject(PortfolioStore);
  private readonly fundStore = inject(FundStore);
  private readonly profiles = inject(TickerProfileStore);
  private readonly router = inject(Router);

  openRun(id: number): void { this.router.navigate(['/runs', id]); }

  book = signal<BookExposureVm | null>(null);

  // Load tracking — distinguish "still fetching" (skeleton) from "loaded but
  // empty" (empty-state). Each flag flips true once the store call settles.
  readonly runsLoaded = signal(false);
  readonly strategiesLoaded = signal(false);
  readonly bookLoaded = signal(false);
  readonly portfolioLoaded = signal(false);

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

  // ── KPI strip formatting (Manual Book, point-in-time) ──────────────────
  private num(v: string | null | undefined): number {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  compact(v: string | null | undefined): string {
    const n = this.num(v);
    const a = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
    return `${sign}$${a.toFixed(2)}`;
  }
  money(v: string | null | undefined): string {
    return `$${this.num(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  }
  signed(v: string | null | undefined): string {
    const n = this.num(v);
    return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  }
  pct(v: string | null | undefined): string {
    return `${this.num(v).toFixed(1)}%`;
  }
  netStr(v: string | null | undefined): string {
    const n = this.num(v);
    return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
  }
  netBias(v: string | null | undefined): string {
    const n = this.num(v);
    if (n > 0.05) return 'long-biased';
    if (n < -0.05) return 'short-biased';
    return 'balanced';
  }
  signTone(v: string | null | undefined): 'up' | 'down' | 'neutral' {
    const n = this.num(v);
    return n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
  }

  /** Decision sig chip from the real RunSummary.decisions[] (empty until a run finishes). */
  runSig(r: RunSummary): { label: string; kind: SigKind } | null {
    const d = r.decisions;
    if (!d || d.length === 0) return null;
    if (d.length > 1) return { label: `${d.length} decisions`, kind: 'info' };
    const map: Record<string, { label: string; kind: SigKind }> = {
      buy: { label: 'buy', kind: 'buy' },
      enter: { label: 'enter', kind: 'buy' },
      hold: { label: 'hold', kind: 'hold' },
      skip: { label: 'skip', kind: 'info' },
      sell: { label: 'sell', kind: 'sell' },
      open_short: { label: 'short', kind: 'sell' },
      cover_short: { label: 'cover', kind: 'info' },
    };
    return map[d[0].action] ?? { label: d[0].action, kind: 'info' };
  }

  ngOnInit(): void {
    this.runs.listRuns().subscribe({
      next: (rows) => { this._prefetchTickerNames(rows); this.runsLoaded.set(true); },
      error: () => this.runsLoaded.set(true),
    });
    this.macro.loadSnapshot().subscribe({ error: () => {} });
    this.portfolio.loadOverview().subscribe({
      next: () => this.portfolioLoaded.set(true),
      error: () => this.portfolioLoaded.set(true),
    });
    this.strategies.list().subscribe({
      next: (ss) => {
        this.strategiesLoaded.set(true);
        // P10 §C1: the exposure widget shows a FUND member's book — not
        // "whichever strategy ran last" (which surfaced the non-fund #56
        // validation sleeve). Iterate fund members (most recent first) and
        // fall back to the legacy heuristic only when no fund exists.
        this.fundStore.loadFund().subscribe({
          next: (f) => {
            const memberIds = new Set((f?.members ?? []).map((a) => a.strategy_id));
            const members = ss.filter((s) => memberIds.has(s.id));
            const ordered = [
              ...members.filter((s) => !!s.last_run_at),
              ...members.filter((s) => !s.last_run_at),
            ];
            const fallback = ss.find((s) => !!s.last_run_at) ?? ss[0];
            this.loadBookFrom(ordered.length ? ordered : (fallback ? [fallback] : []));
          },
          error: () => {
            const fallback = ss.find((s) => !!s.last_run_at) ?? ss[0];
            this.loadBookFrom(fallback ? [fallback] : []);
          },
        });
      },
      error: () => { this.strategiesLoaded.set(true); this.bookLoaded.set(true); },
    });
  }

  /** Try each candidate strategy in order until one has a done cycle. */
  private loadBookFrom(candidates: { id: number; name: string;
    target_gross_pct: string; target_net_pct: string }[]): void {
    const next = candidates[0];
    if (!next) { this.bookLoaded.set(true); return; }
    const rest = candidates.slice(1);
    this.strategies.listCycles(next.id).subscribe({
      next: (cs) => {
        const done = cs.find((c) => c.status === 'done');
        if (!done) { this.loadBookFrom(rest); return; }
        this.strategies.cycleDetail(next.id, done.id).subscribe({
          next: (d) => {
            const allLongs = Object.entries(d.target_weights)
              .filter(([, w]) => Number(w) > 0)
              .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }));
            const allShorts = Object.entries(d.target_weights)
              .filter(([, w]) => Number(w) < 0)
              .map(([ticker, w]) => ({ ticker, weight: Number(w) * 100 }));
            const longPct = allLongs.reduce((a, b) => a + b.weight, 0);
            const shortPct = Math.abs(allShorts.reduce((a, b) => a + b.weight, 0));
            this.book.set({
              strategy_name: next.name,
              strategy_id: next.id,
              as_of_date: d.as_of_date,
              gross_pct: d.gross_pct,
              net_pct: d.net_pct,
              target_gross_pct: next.target_gross_pct,
              target_net_pct: next.target_net_pct,
              longPct,
              shortPct,
              netSigned: longPct - shortPct,
              longCount: allLongs.length,
              shortCount: allShorts.length,
              positionCount: allLongs.length + allShorts.length,
              longs: allLongs.sort((a, b) => b.weight - a.weight).slice(0, 5),
              shorts: allShorts.sort((a, b) => a.weight - b.weight).slice(0, 5),
            });
            this.bookLoaded.set(true);
          },
          error: () => this.loadBookFrom(rest),
        });
      },
      error: () => this.loadBookFrom(rest),
    });
  }
}
