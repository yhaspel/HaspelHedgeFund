import {
  ChangeDetectionStrategy,
  Component,
  Input,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RegimeStore } from '../../abstraction/regime.store';
import { RegimeHistoryItem } from '../../core/models/regime.model';

/**
 * Compact regime context panel rendered on every strategy-detail page.
 * Shows SPY's current Markov state, the strategy's benchmark ticker (if
 * different), the 1-day bull/sideways/bear distribution, and a sparkline of
 * past 60 days of `bull_minus_bear_1d`. Read-only against the prewarm-managed
 * tables.
 */
@Component({
  selector: 'hf-regime-context-widget',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card regime-widget">
      <div class="card-hd">
        <span class="title">Regime context</span>
        <span class="hint" title="Markov regime classifier — a deterministic, price-based regime detector run nightly. Not the council's view.">ⓘ Markov regime</span>
      </div>
      <div class="card-bd">
        <div class="row">
          <div class="cell">
            <div class="label">SPY</div>
            @if (spy(); as s) {
              <span class="pill" [class.bull]="s.current_state === 'bull'"
                                 [class.sideways]="s.current_state === 'sideways'"
                                 [class.bear]="s.current_state === 'bear'">
                <span class="dot"></span>{{ s.current_state }}
                @if (s.stale) { <span class="stale" title="Snapshot older than 10 days">stale</span> }
              </span>
              <div class="meta">
                bull–bear · {{ (s.bull_minus_bear_1d * 100).toFixed(1) }}%
                · stick {{ (s.current_state_persistence * 100).toFixed(0) }}%
              </div>
            } @else {
              <span class="pill"><span class="dot"></span>unavailable</span>
            }
          </div>
          @if (showBenchmark()) {
            <div class="cell">
              <div class="label">{{ benchmarkTicker() }}</div>
              @if (bench(); as b) {
                <span class="pill" [class.bull]="b.current_state === 'bull'"
                                   [class.sideways]="b.current_state === 'sideways'"
                                   [class.bear]="b.current_state === 'bear'">
                  <span class="dot"></span>{{ b.current_state }}
                </span>
                <div class="meta">
                  bull–bear · {{ (b.bull_minus_bear_1d * 100).toFixed(1) }}%
                </div>
              } @else {
                <span class="pill"><span class="dot"></span>unavailable</span>
              }
            </div>
          }
        </div>

        @if (spy(); as s) {
          <div class="bar">
            <div class="seg bull" [style.width.%]="s.bull_prob_1d * 100"
                 [title]="'bull ' + (s.bull_prob_1d*100).toFixed(0) + '%'"></div>
            <div class="seg sideways" [style.width.%]="s.sideways_prob_1d * 100"
                 [title]="'sideways ' + (s.sideways_prob_1d*100).toFixed(0) + '%'"></div>
            <div class="seg bear" [style.width.%]="s.bear_prob_1d * 100"
                 [title]="'bear ' + (s.bear_prob_1d*100).toFixed(0) + '%'"></div>
          </div>
          <div class="legend">
            <span><span class="sw bull"></span>bull {{ (s.bull_prob_1d*100).toFixed(0) }}%</span>
            <span><span class="sw sideways"></span>sideways {{ (s.sideways_prob_1d*100).toFixed(0) }}%</span>
            <span><span class="sw bear"></span>bear {{ (s.bear_prob_1d*100).toFixed(0) }}%</span>
          </div>
        }

        @if (sparklinePath(); as p) {
          <svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none"
               [attr.aria-label]="benchmarkTicker() + ' bull−bear 60d'">
            <path [attr.d]="p" />
            <line x1="0" y1="15" x2="100" y2="15" />
          </svg>
        }
      </div>
    </section>
  `,
  styles: [`
    .regime-widget { margin-bottom: 16px; }
    .row { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; margin-bottom: 10px; }
    .cell { min-width: 130px; }
    .label { font-size: 11px; opacity: 0.6; letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 4px; }
    .pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;
            border-radius: var(--r-full); font-size: 12px; font-weight: 500;
            background: var(--surface-2); color: var(--text-2); }
    .pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-3); }
    .pill.bull { background: var(--acc-long-soft); color: var(--acc-long-fg); }
    .pill.bull .dot { background: var(--acc-long); }
    .pill.sideways { background: var(--acc-hold-soft); color: var(--acc-hold-fg); }
    .pill.sideways .dot { background: var(--acc-hold); }
    .pill.bear { background: var(--acc-short-soft); color: var(--acc-short-fg); }
    .pill.bear .dot { background: var(--acc-short); }
    .pill .stale { font-size: 10px; padding: 1px 6px; border-radius: var(--r-4);
                   background: var(--acc-hold-soft); color: var(--acc-hold-fg); margin-left: 6px; }
    .meta { font-size: 11px; opacity: 0.7; margin-top: 4px; }
    .bar { display: flex; height: 8px; border-radius: var(--r-4); overflow: hidden;
           background: var(--surface-2); margin-top: 6px; }
    .bar .seg { transition: width 240ms ease-out; }
    .bar .seg.bull { background: var(--acc-long); }
    .bar .seg.sideways { background: var(--acc-hold); }
    .bar .seg.bear { background: var(--acc-short); }
    .legend { display: flex; gap: 12px; font-size: 11px; opacity: 0.75; margin-top: 6px; }
    .legend .sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px;
                  margin-right: 4px; vertical-align: middle; }
    .legend .sw.bull { background: var(--acc-long); }
    .legend .sw.sideways { background: var(--acc-hold); }
    .legend .sw.bear { background: var(--acc-short); }
    .spark { width: 100%; height: 30px; margin-top: 8px; display: block; }
    .spark path { fill: none; stroke: var(--acc-info); stroke-width: 1.2; }
    .spark line { stroke: var(--border-2); stroke-dasharray: 2 3; stroke-width: 0.5; }
    .hint { font-size: 11px; opacity: 0.6; cursor: help; }
  `],
})
export class RegimeContextWidgetComponent implements OnInit {
  private readonly _benchmark = signal('SPY');
  private readonly _asOf = signal<string | undefined>(undefined);

  /** Strategy's benchmark / primary ticker. Defaults to SPY. */
  @Input() set benchmark(v: string | null | undefined) {
    this._benchmark.set((v || 'SPY').toUpperCase());
  }
  @Input() set asOf(v: string | null | undefined) {
    this._asOf.set(v || undefined);
  }

  private readonly store = inject(RegimeStore);

  readonly benchmarkTicker = computed(() => this._benchmark());
  readonly spy = computed(() => this.store.byTicker()['SPY'] ?? null);
  readonly bench = computed(
    () => this.store.byTicker()[this._benchmark()] ?? null
  );
  readonly showBenchmark = computed(() => this._benchmark() !== 'SPY');

  readonly sparklinePath = computed(() => {
    const h = this.store.history();
    if (!h || h.items.length < 2) return '';
    return this._buildSparklinePath(h.items);
  });

  ngOnInit(): void {
    this._refresh();
  }

  ngOnChanges(): void {
    this._refresh();
  }

  private _refresh(): void {
    const ticker = this._benchmark();
    const asOf = this._asOf();
    const tickers = ticker === 'SPY' ? ['SPY'] : ['SPY', ticker];
    this.store.loadBatch(tickers, asOf).subscribe({ error: () => {} });
    const to = asOf || new Date().toISOString().slice(0, 10);
    const from = this._daysBack(to, 60);
    this.store.loadHistory(ticker, from, to).subscribe({ error: () => {} });
  }

  private _daysBack(iso: string, n: number): string {
    const d = new Date(iso + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() - n);
    return d.toISOString().slice(0, 10);
  }

  private _buildSparklinePath(items: RegimeHistoryItem[]): string {
    const xs: number[] = items.map((_, i) => (i / Math.max(1, items.length - 1)) * 100);
    const ys: number[] = items.map((it) => 15 - it.bull_minus_bear_1d * 14);
    let path = '';
    for (let i = 0; i < items.length; i++) {
      path += (i === 0 ? 'M' : 'L') + xs[i].toFixed(2) + ' ' + ys[i].toFixed(2) + ' ';
    }
    return path.trim();
  }
}
