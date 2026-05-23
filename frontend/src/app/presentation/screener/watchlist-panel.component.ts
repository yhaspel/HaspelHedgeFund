import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { WatchlistItem } from '../../core/models/screener.model';
import { TickerComponent } from '../shared/ticker.component';
import { EmptyStateComponent } from '../shared/empty-state.component';

@Component({
  selector: 'hf-screener-watchlist-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, TickerComponent, EmptyStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="card">
      <div class="card-hd">
        <span class="title">Watchlist</span>
        <div class="actions">
          <input
            class="input mono"
            type="text"
            [(ngModel)]="newTicker"
            placeholder="ADD TICKER"
            (input)="newTicker = newTicker.toUpperCase()"
            (keydown.enter)="onAdd()"
            aria-label="Add ticker to watchlist"
          />
          <button
            type="button"
            class="btn sm"
            [disabled]="!newTicker.trim()"
            (click)="onAdd()"
          >Add</button>
        </div>
      </div>
      <div class="card-bd">
        @if (!items.length) {
          <hf-empty-state
            message="No tickers on your watchlist yet."
            detail="Star a row in the results table or type a symbol above to add one."
          />
        } @else {
          <div class="tbl-wrap">
            <table class="tbl">
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col" class="right">Price</th>
                  <th scope="col" class="right">Change %</th>
                  <th scope="col" class="right">RVOL</th>
                  <th scope="col" class="right">Volume</th>
                  <th scope="col" class="right">Market Cap</th>
                  <th scope="col" class="right">Added</th>
                  <th scope="col" class="right"><span class="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                @for (it of items; track it.ticker) {
                  <tr>
                    <td><hf-ticker [ticker]="it.ticker"></hf-ticker></td>
                    <td class="num mono">{{ formatDecimal(it.price) }}</td>
                    <td class="num mono" [class.long]="(it.change_pct ?? 0) > 0" [class.short]="(it.change_pct ?? 0) < 0">
                      {{ formatNumber(it.change_pct) }}%
                    </td>
                    <td class="num mono">{{ formatNumber(it.rvol) }}</td>
                    <td class="num mono">{{ formatInt(it.volume) }}</td>
                    <td class="num mono">{{ formatBig(it.market_cap) }}</td>
                    <td class="num mono small">{{ formatDate(it.added_at) }}</td>
                    <td class="right action-cell">
                      <button
                        type="button"
                        class="btn ghost sm"
                        (click)="addToPortfolio.emit(it)"
                      >+ Book</button>
                      <button
                        type="button"
                        class="btn ghost sm"
                        (click)="sendToRun.emit(it)"
                      >Analyze →</button>
                      <button
                        type="button"
                        class="btn ghost sm danger"
                        (click)="remove.emit(it)"
                        aria-label="Remove from watchlist"
                      >×</button>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      </div>
    </section>
  `,
  styles: [
    `
      .tbl-wrap { overflow-x: auto; }
      .num {
        text-align: right;
        font-variant-numeric: tabular-nums;
      }
      .long { color: var(--acc-long-fg); }
      .short { color: var(--acc-short-fg); }
      .small { font-size: var(--fs-11); color: var(--text-3); }
      .action-cell {
        display: flex;
        gap: 4px;
        justify-content: flex-end;
      }
      .btn.danger { color: var(--acc-short-fg); }
      .actions { margin-left: auto; display: flex; gap: 6px; }
      .actions .input { width: 140px; }
      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
      }
    `,
  ],
})
export class WatchlistPanelComponent {
  @Input() items: WatchlistItem[] = [];
  @Output() add = new EventEmitter<string>();
  @Output() remove = new EventEmitter<WatchlistItem>();
  @Output() addToPortfolio = new EventEmitter<WatchlistItem>();
  @Output() sendToRun = new EventEmitter<WatchlistItem>();

  newTicker = '';

  onAdd(): void {
    const t = this.newTicker.trim().toUpperCase();
    if (!t) return;
    this.add.emit(t);
    this.newTicker = '';
  }

  formatDecimal(v: string | null | undefined): string {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v);
    return Number.isFinite(n)
      ? n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : '—';
  }

  formatNumber(v: number | null | undefined): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return v.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  formatInt(v: number | null | undefined): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return Math.round(v).toLocaleString('en-US');
  }

  formatBig(v: string | null | undefined): string {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    return n.toLocaleString('en-US');
  }

  formatDate(iso: string): string {
    try {
      return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch {
      return iso;
    }
  }
}
