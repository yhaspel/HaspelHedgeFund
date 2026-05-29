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
            account, and each strategy book. Open any row to manage it.
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
        <section class="kpi-row mb-4">
          <hf-kpi-tile eyebrow="Total cash"
                       [value]="'$' + (+hub.totals.cash | number: '1.2-2')"
                       sub="across all books" />
          <hf-kpi-tile eyebrow="Total equity"
                       [value]="'$' + (+hub.totals.equity | number: '1.2-2')"
                       sub="cash + positions at cost" />
          <hf-kpi-tile eyebrow="Books"
                       [value]="hub.totals.books.toString()"
                       [sub]="hub.totals.books === 1 ? 'portfolio' : 'portfolios'" />
        </section>

        <section class="card p-0 overflow-hidden">
          <div class="card-hd"><h2 class="title">Your books</h2></div>
          @if (hub.books.length === 0) {
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
                @for (book of hub.books; track book.portfolio_id) {
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
                      <span class="pill" [class.info]="book.kind === 'manual'"
                            [class.ok]="book.kind === 'broker'"
                            [class.warn]="book.kind === 'strategy'">
                        <span class="dot"></span>{{ kindLabel(book.kind) }}
                      </span>
                    </td>
                    <td class="text-right mono">{{ '$' + (+book.cash | number: '1.2-2') }}</td>
                    <td class="text-right mono">{{ '$' + (+book.market_value | number: '1.2-2') }}</td>
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

  ngOnInit(): void {
    this.store.loadHub().subscribe({
      next: () => this.loading.set(false),
      error: (e) => {
        this.loadError.set(e?.error?.detail || 'Failed to load portfolios.');
        this.loading.set(false);
      },
    });
  }

  kindLabel(kind: string): string {
    if (kind === 'manual') return 'Manual';
    if (kind === 'broker') return 'Broker';
    return 'Strategy';
  }
}
