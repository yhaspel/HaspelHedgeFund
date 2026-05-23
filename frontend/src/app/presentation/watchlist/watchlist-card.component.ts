/**
 * Compact watchlist card used on the Dashboard and on the Profile page.
 * The full-page surface lives in watchlist.page.ts.
 */
import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { WatchlistStore } from '../../abstraction/watchlist.store';
import { EmptyStateComponent } from '../shared/empty-state.component';
import { TickerComponent } from '../shared/ticker.component';

@Component({
  selector: 'hf-watchlist-card',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    EmptyStateComponent,
    TickerComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <div class="card-hd">
        <span class="title">Watchlist</span>
        <a routerLink="/watchlist" class="link sm" aria-label="Manage watchlist">
          Manage →
        </a>
      </div>
      <div class="card-bd">
        @if (store.busy() && store.items().length === 0) {
          <div class="text-text-3 text-[12px]">Loading…</div>
        } @else if (store.items().length === 0) {
          <hf-empty-state
            message="No tickers yet."
            detail="Add one below, or complete the investor questionnaire to seed favorites."
          />
        } @else {
          <ul class="watchlist-list">
            @for (it of store.items().slice(0, 8); track it.ticker) {
              <li>
                <hf-ticker [ticker]="it.ticker"></hf-ticker>
                <span class="num mono"
                      [class.long]="(it.change_pct ?? 0) > 0"
                      [class.short]="(it.change_pct ?? 0) < 0">
                  {{ formatChange(it.change_pct) }}
                </span>
                <button
                  type="button"
                  class="btn ghost sm"
                  (click)="onAnalyze(it.ticker)"
                  aria-label="Analyze in New Run"
                >Analyze →</button>
              </li>
            }
          </ul>
          @if (store.items().length > 8) {
            <a routerLink="/watchlist" class="more">+{{ store.items().length - 8 }} more</a>
          }
        }
        <div class="add-row" [class.disabled]="store.items().length >= 100">
          <input
            class="input mono"
            type="text"
            [(ngModel)]="newTicker"
            placeholder="ADD TICKER"
            (input)="newTicker = newTicker.toUpperCase()"
            (keydown.enter)="onAdd()"
            aria-label="Add ticker"
            [disabled]="store.items().length >= 100"
          />
          <button
            type="button"
            class="btn sm"
            [disabled]="!newTicker.trim() || store.items().length >= 100"
            (click)="onAdd()"
          >Add</button>
        </div>
      </div>
    </section>
  `,
  styles: [
    `
      .watchlist-list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .watchlist-list li {
        display: grid;
        grid-template-columns: 1fr auto auto;
        align-items: center;
        gap: 10px;
        padding: 6px 8px;
        border-radius: var(--r-4);
      }
      .watchlist-list li:hover { background: var(--surface-2); }
      .num { font-variant-numeric: tabular-nums; }
      .long { color: var(--acc-long-fg); }
      .short { color: var(--acc-short-fg); }
      .add-row {
        display: flex;
        gap: 6px;
        margin-top: 12px;
      }
      .add-row .input { flex: 1; }
      .add-row.disabled { opacity: 0.6; }
      .link.sm { font-size: 12px; }
      .more {
        display: inline-block;
        margin-top: 8px;
        font-size: 11px;
        color: var(--text-3);
      }
    `,
  ],
})
export class WatchlistCardComponent implements OnInit {
  readonly store = inject(WatchlistStore);
  private readonly router = inject(Router);

  newTicker = '';

  ngOnInit(): void {
    if (!this.store.loaded()) {
      this.store.load().subscribe();
    }
  }

  onAdd(): void {
    const t = this.newTicker.trim().toUpperCase();
    if (!t) return;
    this.store.add(t).subscribe({
      next: () => (this.newTicker = ''),
      error: () => undefined,
    });
  }

  onAnalyze(ticker: string): void {
    this.router.navigate(['/runs/new'], { queryParams: { ticker } });
  }

  formatChange(v: number | null | undefined): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    const sign = v >= 0 ? '+' : '';
    return `${sign}${v.toFixed(2)}%`;
  }
}
