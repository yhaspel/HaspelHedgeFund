import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges, computed, inject, signal } from '@angular/core';

import { ProvenanceStore } from '../../abstraction/provenance.store';
import {
  ProvenanceTicker,
  ageLabel,
  freshnessOf,
} from '../../core/models/provenance.model';
import { ErrorStateComponent } from './error-state.component';

/**
 * WAVE 3 item 1 — "where did this number come from, and how old is it?".
 *
 * The highest-value panel in the wave: every silent-staleness bug in the app
 * (a bar series that stopped updating, a regime snapshot classified off a
 * stale price date, a provider key that quietly went missing) is invisible
 * until something like this renders it. Three placements share the component:
 *   • per-ticker rows (run detail, a position's context),
 *   • the global macro + provider block (Settings › Data & News),
 *   • both at once, when a surface has tickers AND wants the provider state.
 *
 * "Refresh now" posts to `/data/provenance/refresh/` and flips straight into
 * an optimistic "queued" state — the endpoint only enqueues Celery tasks, so
 * there is nothing to await. A 503 (broker down) surfaces its `detail`.
 */
@Component({
  selector: 'hf-provenance',
  standalone: true,
  imports: [CommonModule, ErrorStateComponent],
  template: `
    <section class="prov card" data-test="provenance">
      <div class="card-hd">
        <h3 class="title">{{ heading }}</h3>
        <div class="actions">
          @if (store.asOf(); as a) {
            <span class="asof" data-test="provenance-as-of">read {{ a | date: 'HH:mm' }}</span>
          }
          @if (tickerList().length) {
            <button
              type="button"
              class="btn sm"
              data-test="provenance-refresh"
              [disabled]="store.refreshing()"
              (click)="refresh()"
            >
              {{ store.refreshing() ? 'Queueing…' : 'Refresh now' }}
            </button>
          }
        </div>
      </div>

      <div class="card-bd">
        @if (store.error(); as e) {
          <hf-error-state
            [compact]="true"
            title="Couldn’t read data provenance"
            [detail]="e"
            (retry)="reload()"
          ></hf-error-state>
        } @else if (store.loading() && !rows().length && !store.global()) {
          <p class="muted" data-test="provenance-loading">Reading data provenance…</p>
        } @else {
          @if (store.refreshError(); as re) {
            <p class="refresh-err" role="alert" data-test="provenance-refresh-error">{{ re }}</p>
          } @else if (queuedHere().length) {
            <p class="refresh-ok" role="status" data-test="provenance-queued">
              Refresh queued for {{ queuedHere().join(', ') }}. Re-read this panel in a minute —
              the workers update the store in the background.
            </p>
          }

          @if (tickerList().length && !rows().length) {
            <p class="muted" data-test="provenance-empty">
              No stored data for {{ tickerList().join(', ') }} yet.
            </p>
          }

          @for (r of rows(); track r.ticker) {
            <div class="row" [attr.data-test]="'provenance-row-' + r.ticker">
              <div class="sym">{{ r.ticker }}</div>
              <dl class="facts">
                <div class="fact">
                  <dt>Last bar</dt>
                  <dd>
                    <span class="mono">{{ r.bars?.last_date || '—' }}</span>
                    <span
                      class="badge"
                      [class.fresh]="level(r.bars?.last_date) === 'fresh'"
                      [class.aging]="level(r.bars?.last_date) === 'aging'"
                      [class.stale]="level(r.bars?.last_date) === 'stale'"
                      [attr.data-test]="'provenance-bar-age-' + r.ticker"
                      >{{ age(r.bars?.last_date) }}</span
                    >
                    @if (r.bars?.source) {
                      <span class="src" [attr.data-test]="'provenance-bar-source-' + r.ticker"
                        >via {{ r.bars?.source }}</span
                      >
                    }
                    <span class="src">{{ r.bars?.count || 0 }} bars</span>
                  </dd>
                </div>
                <div class="fact">
                  <dt>Adjusted close</dt>
                  <dd [attr.data-test]="'provenance-adjusted-' + r.ticker">
                    @if (r.bars?.adjusted_differs_from_close) {
                      <span class="badge fresh">total-return adjusted</span>
                    } @else {
                      <span class="badge aging">equals close — price-return only</span>
                    }
                  </dd>
                </div>
                <div class="fact">
                  <dt>Last dividend</dt>
                  <dd [attr.data-test]="'provenance-dividends-' + r.ticker">
                    <span class="mono">{{ r.dividends.last_ex_date || 'none stored' }}</span>
                    <span class="src">{{ r.dividends.count }} cash dividend(s)</span>
                  </dd>
                </div>
                <div class="fact">
                  <dt>Filings</dt>
                  <dd [attr.data-test]="'provenance-filings-' + r.ticker">
                    <span class="mono">{{ r.filings.count }}</span>
                    <span class="src"
                      >newest {{ r.filings.newest_filed_at
                        ? (r.filings.newest_filed_at | date: 'y-MM-dd')
                        : '—' }}</span
                    >
                  </dd>
                </div>
                <div class="fact">
                  <dt>News</dt>
                  <dd [attr.data-test]="'provenance-news-' + r.ticker">
                    @if (!r.news.length) {
                      <span class="src">no stored news</span>
                    }
                    @for (n of r.news; track n.provider) {
                      <span class="src"
                        >{{ n.provider }}: {{ n.count }} ·
                        {{ age(n.newest_published_at) }}</span
                      >
                    }
                  </dd>
                </div>
                <div class="fact">
                  <dt>Regime</dt>
                  <dd [attr.data-test]="'provenance-regime-' + r.ticker">
                    @if (r.regime; as g) {
                      <span class="mono">{{ g.as_of_date }}</span>
                      <span class="src">priced to {{ g.last_price_date }}</span>
                      <span class="src">{{ g.model_type }}</span>
                      @if (g.stale) {
                        <span class="badge stale" [attr.data-test]="'provenance-regime-stale-' + r.ticker"
                          >stale</span
                        >
                      } @else {
                        <span class="badge fresh">current</span>
                      }
                      @if (g.as_of_date !== g.last_price_date) {
                        <span class="src warn"
                          >classified {{ g.as_of_date }} off prices through
                          {{ g.last_price_date }}</span
                        >
                      }
                    } @else {
                      <span class="src">no regime snapshot</span>
                    }
                  </dd>
                </div>
              </dl>
            </div>
          }

          @if (showGlobal && store.global(); as g) {
            <div class="row global" data-test="provenance-global">
              <div class="sym">Macro</div>
              <dl class="facts">
                <div class="fact">
                  <dt>Snapshot</dt>
                  <dd data-test="provenance-macro-snapshot">
                    <span class="mono">{{ g.macro.snapshot_as_of || 'none' }}</span>
                    <span class="src"
                      >classifier {{ g.macro.classifier_version || 'unknown' }}</span
                    >
                  </dd>
                </div>
                <div class="fact wide">
                  <dt>Series vintages</dt>
                  <dd data-test="provenance-macro-series">
                    @if (!g.macro.series.length) {
                      <span class="src">no macro series stored</span>
                    }
                    @for (s of g.macro.series; track s.series_id) {
                      <span class="src"
                        >{{ s.series_id }}: obs {{ s.newest_observation_date }} · vintage
                        {{ s.newest_vintage_date }}</span
                      >
                    }
                  </dd>
                </div>
              </dl>
            </div>

            <div class="row global" data-test="provenance-providers">
              <div class="sym">Providers</div>
              <dl class="facts">
                @for (p of providerRows(); track p.name) {
                  <div class="fact" [attr.data-test]="'provenance-provider-' + p.name">
                    <dt>{{ p.name }}</dt>
                    <dd>
                      <span
                        class="badge"
                        [class.fresh]="p.key === 'configured'"
                        [class.stale]="p.key !== 'configured'"
                        >{{ p.key === 'configured' ? 'key configured' : 'no key' }}</span
                      >
                      @if (p.userByok) {
                        <span class="badge fresh">your key</span>
                      }
                      @if (p.hasFreshness) {
                        <span
                          class="badge"
                          [class.fresh]="p.level === 'fresh'"
                          [class.aging]="p.level === 'aging'"
                          [class.stale]="p.level === 'stale'"
                          >last success {{ p.age }}</span
                        >
                        <span class="src">{{ p.count }} row(s)</span>
                      } @else {
                        <span class="src">no data freshness tracked</span>
                      }
                    </dd>
                  </div>
                }
                <div class="fact wide">
                  <dt>Key policy</dt>
                  <dd data-test="provenance-policy">
                    @if (g.policy.allow_platform_data_keys) {
                      <span class="src"
                        >Platform data keys are allowed — a provider with no BYO key still
                        fetches.</span
                      >
                    } @else {
                      <span class="src warn"
                        >Platform data keys are OFF — a provider without your own key will not
                        fetch, and its rows above simply stop ageing.</span
                      >
                    }
                  </dd>
                </div>
              </dl>
            </div>
          }
        }
      </div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .card-hd .asof {
        font-size: var(--fs-11);
        color: var(--text-3);
        font-family: var(--font-mono);
      }
      .card-bd {
        padding: 12px 16px;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .muted {
        margin: 0;
        font-size: var(--fs-12);
        color: var(--text-3);
      }
      .refresh-ok,
      .refresh-err {
        margin: 0;
        font-size: var(--fs-12);
        padding: 6px 10px;
        border-radius: var(--r-6);
      }
      .refresh-ok {
        background: var(--acc-info-soft);
        color: var(--acc-info-fg);
      }
      .refresh-err {
        background: var(--acc-short-soft);
        color: var(--acc-short-fg);
      }
      .row {
        display: flex;
        gap: 12px;
        align-items: flex-start;
        padding-top: 10px;
        border-top: 1px solid var(--border);
      }
      .row:first-of-type {
        border-top: 0;
        padding-top: 0;
      }
      .sym {
        min-width: 68px;
        font-family: var(--font-mono);
        font-size: var(--fs-13);
        font-weight: 600;
        color: var(--text);
        letter-spacing: var(--tracking-mono);
      }
      .facts {
        margin: 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 8px 16px;
        flex: 1;
      }
      .fact.wide {
        grid-column: 1 / -1;
      }
      dt {
        font-size: var(--fs-11);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--text-3);
        margin-bottom: 2px;
      }
      dd {
        margin: 0;
        display: flex;
        flex-wrap: wrap;
        gap: 4px 8px;
        align-items: center;
        font-size: var(--fs-12);
        color: var(--text-2);
      }
      .mono {
        font-family: var(--font-mono);
        color: var(--text);
      }
      .src {
        color: var(--text-3);
      }
      .src.warn {
        color: var(--acc-hold-fg);
      }
      .badge {
        display: inline-flex;
        align-items: center;
        padding: 1px 6px;
        border-radius: var(--r-full);
        font-size: var(--fs-11);
        border: 1px solid var(--border-2);
        color: var(--text-3);
      }
      .badge.fresh {
        background: var(--acc-long-soft);
        color: var(--acc-long-fg);
        border-color: var(--acc-long);
      }
      .badge.aging {
        background: var(--acc-hold-soft);
        color: var(--acc-hold-fg);
        border-color: var(--acc-hold);
      }
      .badge.stale {
        background: var(--acc-short-soft);
        color: var(--acc-short-fg);
        border-color: var(--acc-short);
      }
    `,
  ],
})
export class ProvenancePanelComponent implements OnChanges {
  readonly store = inject(ProvenanceStore);

  /** Symbols to show a per-ticker row for. Empty ⇒ global block only. */
  @Input() tickers: readonly string[] = [];
  /** Render the macro + provider-key block (Settings › Data & News uses this). */
  @Input() showGlobal = false;
  @Input() heading = 'Data provenance';
  /** Skip the auto-load (a host that already primed the store). */
  @Input() autoLoad = true;

  private readonly _tickers = signal<string[]>([]);
  readonly tickerList = this._tickers.asReadonly();

  readonly rows = computed<ProvenanceTicker[]>(() => this.store.rowsFor(this._tickers()));

  readonly queuedHere = computed(() => {
    const mine = this._tickers();
    return this.store.queued().filter((t) => mine.includes(t));
  });

  readonly providerRows = computed(() => {
    const g = this.store.global();
    if (!g) return [];
    return Object.entries(g.providers ?? {}).map(([name, p]) => {
      const last = p.freshness?.last_at ?? null;
      return {
        name,
        key: p.key,
        userByok: p.user_byok,
        hasFreshness: !!p.freshness,
        age: ageLabel(last),
        level: freshnessOf(last),
        count: p.freshness?.count ?? 0,
      };
    });
  });

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['tickers']) {
      const next = ProvenanceStore.normalize(this.tickers ?? []);
      const prev = this._tickers();
      const same = next.length === prev.length && next.every((t, i) => t === prev[i]);
      if (!same) {
        this._tickers.set(next);
        if (this.autoLoad) this.reload();
        return;
      }
    }
    if (changes['showGlobal']?.firstChange && this.autoLoad && !this.tickers?.length) {
      this.reload();
    }
  }

  reload(): void {
    this.store.load(this._tickers()).subscribe();
  }

  refresh(): void {
    const list = this._tickers();
    if (!list.length) return;
    this.store.refresh(list).subscribe({ error: () => undefined });
  }

  level(iso: string | null | undefined) {
    return freshnessOf(iso);
  }

  age(iso: string | null | undefined): string {
    return ageLabel(iso);
  }
}
