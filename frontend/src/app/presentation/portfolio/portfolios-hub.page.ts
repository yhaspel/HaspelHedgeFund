import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AppShellComponent } from '../shared/app-shell.component';
import { KpiTileComponent } from '../shared/kpi-tile.component';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { PortfolioStore } from '../../abstraction/portfolio.store';

/**
 * Portfolios hub — one place to see every book the user owns: the manual
 * book, each broker account's book, and each strategy book. Every row
 * links to the surface that actually manages that kind of book.
 */
@Component({
  selector: 'hf-portfolios-hub-page',
  standalone: true,
  imports: [
    CommonModule, DecimalPipe, RouterLink,
    AppShellComponent, KpiTileComponent, EmptyStateComponent,
  ],
  template: `
    <hf-app-shell [crumbs]="[{label:'Portfolios'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Overview</div>
          <h1 class="mt-1.5">Portfolios</h1>
          <p class="text-text-3 text-[12.5px] mt-1 max-w-[640px]">
            Every book you own — your hand-managed Manual book, each broker
            account, and each strategy book you've entered. Positions shown are
            what each book currently holds (a strategy's per-cycle target lives
            on the strategy page). Strategy books appear here once they hold something.
          </p>
        </div>
      </div>

      @if (loadError(); as e) {
        <div role="alert" class="pill err h-auto py-2 px-3 mb-3.5">
          <span class="dot"></span>{{ e }}
        </div>
      }

      @if (loading()) {
        <div class="card p-4 text-xs text-text-3">Loading portfolios…</div>
      } @else if (store.hub(); as hub) {
        <!-- P10 §C5: headline totals = REAL capital only (broker + manual,
             marked to market). Strategy mirrors are parallel paper notional —
             the old all-books cost-basis sum overstated real capital ~2.65×. -->
        <section class="kpi-row mb-4">
          <hf-kpi-tile eyebrow="Total cash"
                       [value]="'$' + (+hub.totals.cash | number: '1.2-2')"
                       sub="broker + manual books" />
          <hf-kpi-tile eyebrow="Total equity"
                       [value]="'$' + (+hub.totals.equity | number: '1.2-2')"
                       sub="broker + manual · marked to market" />
          <hf-kpi-tile eyebrow="Books"
                       [value]="hub.totals.books.toString()"
                       [sub]="(+(hub.totals.mirror_books || 0)) > 0
                          ? ('incl. ' + hub.totals.mirror_books + ' strategy paper books')
                          : (hub.totals.books === 1 ? 'portfolio' : 'portfolios')" />
        </section>

        <section class="card p-0 overflow-hidden">
          <div class="card-hd">
            <h2 class="title">Your books</h2>
            @if ((+(hub.totals.mirror_books || 0)) > 0) {
              <button type="button" class="chip" [class.chip-on]="showMirrors()"
                      (click)="showMirrors.set(!showMirrors())"
                      data-test="hub-mirror-toggle">
                Strategy paper books ({{ hub.totals.mirror_books }}) —
                {{ '$' + (+(hub.totals.mirror_equity || '0') | number: '1.0-0') }} notional
              </button>
            }
          </div>
          @if (visibleBooks(hub).length === 0) {
            <hf-empty-state message="No portfolios yet"
              detail="Your Manual book will appear here automatically." />
          } @else {
            <table class="tbl w-full" data-test="hub-table">
              <thead>
                <tr>
                  <th class="text-left">Book</th>
                  <th class="text-left">Kind</th>
                  <th class="text-right">Cash</th>
                  <th class="text-right">Market value</th>
                  <th class="text-right">Equity</th>
                  <th class="text-right">Positions</th>
                  <th class="text-right"><span class="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody>
                @for (book of visibleBooks(hub); track book.portfolio_id) {
                  <tr class="book-row" [routerLink]="book.link_route"
                      [attr.data-test]="'hub-row-' + book.kind + '-' + book.portfolio_id">
                    <td>
                      <div class="font-medium">{{ book.name }}</div>
                      <div class="text-[11px] text-text-3">{{ book.subtitle }}</div>
                      @if (book.kind === 'strategy' && book.positions_count === 0) {
                        <div class="text-[11px] text-[var(--acc-info-fg)]"
                             data-test="enrol-hint">
                          No positions yet — enter the strategy from a done cycle.
                        </div>
                      }
                    </td>
                    <td>
                      <!-- §3.5: the demo (mock) broker book is a historical
                           artifact post-P10 — a plain gray pill, not the
                           broker-green of real accounts. -->
                      <span class="pill" [class.info]="book.kind === 'manual' || book.kind === 'sleeve'"
                            [class.ok]="book.kind === 'broker' && !isDemo(book)"
                            [class.warn]="book.kind === 'strategy'">
                        <span class="dot"></span>{{ kindLabel(book) }}
                      </span>
                    </td>
                    <td class="text-right mono">{{ '$' + (+book.cash | number: '1.2-2') }}</td>
                    <td class="text-right mono" [title]="book.marked ? 'Marked to market' : 'Cost basis (mark unavailable)'">
                      {{ '$' + (+book.market_value | number: '1.2-2') }}@if (!book.marked && book.positions_count > 0) {<span class="text-text-3">*</span>}
                    </td>
                    <td class="text-right mono font-medium">{{ '$' + (+book.equity | number: '1.2-2') }}</td>
                    <td class="text-right mono">{{ book.positions_count }}</td>
                    <td class="text-right">
                      <span class="open-link">Open
                        <svg width="13" height="13" aria-hidden="true" class="ml-0.5">
                          <use href="/icons.svg#i-arrow-rt" /></svg>
                      </span>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>
      }
    </hf-app-shell>
  `,
  styles: [`
    .book-row { cursor: pointer; }
    .book-row:hover { background: var(--surface-2); }
    .chip {
      background: transparent; border: 1px solid var(--border); color: var(--text-2);
      font-size: 11px; line-height: 1; padding: 0 10px; min-height: 24px;
      display: inline-flex; align-items: center; border-radius: var(--r-full); cursor: pointer;
    }
    .chip:hover { background: var(--surface-2); }
    .chip:focus-visible { outline: none; box-shadow: var(--focus-ring); }
    .chip-on { background: var(--surface-2); color: var(--text); border-color: var(--text-3); }
    .open-link {
      display: inline-flex; align-items: center;
      color: var(--text-3); font-size: var(--fs-12);
    }
    .book-row:hover .open-link { color: var(--text); }
    .sr-only {
      position: absolute; width: 1px; height: 1px;
      overflow: hidden; clip: rect(0, 0, 0, 0);
    }
  `],
})
export class PortfoliosHubPage implements OnInit {
  readonly store = inject(PortfolioStore);
  readonly loading = signal(true);
  readonly loadError = signal<string | null>(null);
  // P10 §C5: strategy paper books (mirrors) hidden by default — they are an
  // implementation detail of pods; the fund accounts are what matters.
  readonly showMirrors = signal(false);

  ngOnInit(): void {
    this.store.loadHub().subscribe({
      next: () => this.loading.set(false),
      error: (e) => {
        this.loadError.set(e?.error?.detail || 'Failed to load portfolios.');
        this.loading.set(false);
      },
    });
  }

  // P14: fund sleeves are attribution slices of the broker book (mirrors too) —
  // hidden with the strategy mirrors by default, never counted twice.
  visibleBooks(hub: { books: { kind: string }[] }): any[] {
    const books = hub.books as any[];
    return this.showMirrors()
      ? books
      : books.filter((b) => b.kind !== 'strategy' && b.kind !== 'sleeve');
  }

  isDemo(book: { kind: string; broker?: string }): boolean {
    return book.kind === 'broker' && book.broker === 'mock';
  }

  kindLabel(book: { kind: string; broker?: string }): string {
    if (book.kind === 'manual') return 'Manual';
    if (book.kind === 'broker') return this.isDemo(book) ? 'Demo' : 'Broker';
    if (book.kind === 'sleeve') return 'Fund sleeve';
    return 'Strategy';
  }
}
