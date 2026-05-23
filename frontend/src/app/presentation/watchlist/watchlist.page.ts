import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';

import { WatchlistStore } from '../../abstraction/watchlist.store';
import { WatchlistItem } from '../../core/models/screener.model';
import { EnterPositionModalComponent } from '../portfolio/enter-position.modal';
import { AppShellComponent } from '../shared/app-shell.component';
import { WatchlistPanelComponent } from './watchlist-panel.component';

@Component({
  selector: 'hf-watchlist-page',
  standalone: true,
  imports: [
    CommonModule,
    AppShellComponent,
    EnterPositionModalComponent,
    WatchlistPanelComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{label:'Watchlist'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Discovery</div>
          <h1 class="mt-1.5">Watchlist</h1>
          <p class="text-text-3 text-[12.5px] mt-1 max-w-[640px]">
            Tickers you're tracking. Star new ones from the Screener, type a
            symbol below, or let the investor questionnaire pre-fill it from
            your favorites.
          </p>
        </div>
        <div class="head-right text-text-3 text-[12px]">
          {{ store.items().length }} of 100
        </div>
      </div>
      @if (store.error()) {
        <p class="alert" role="alert" aria-live="polite">{{ store.error() }}</p>
      }
      <hf-screener-watchlist-panel
        [items]="store.items()"
        (add)="onAdd($event)"
        (remove)="onRemove($event)"
        (addToPortfolio)="onAddToPortfolio($event)"
        (sendToRun)="onSendToRun($event)"
      />
      <hf-enter-position-modal
        [open]="entryOpen()"
        [prefill]="entryPrefill()"
        (closed)="onEntryClosed()"
      />
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 16px;
      }
      .alert {
        padding: 10px 14px;
        background: var(--acc-short-soft);
        color: var(--acc-short-fg);
        border-radius: var(--r-6);
        margin: 0 0 12px;
      }
    `,
  ],
})
export class WatchlistPage implements OnInit {
  readonly store = inject(WatchlistStore);
  private readonly router = inject(Router);

  readonly entryOpen = signal(false);
  readonly entryPrefill = signal<{ ticker: string; initialPrice?: number } | null>(
    null,
  );

  ngOnInit(): void {
    this.store.load().subscribe();
  }

  onAdd(ticker: string): void {
    this.store.add(ticker).subscribe({
      error: (e) => {
        const detail = e?.error?.detail ?? e?.message ?? 'Failed to add ticker';
        this.store.setError(String(detail));
      },
    });
  }

  onRemove(it: WatchlistItem): void {
    this.store.remove(it.ticker).subscribe();
  }

  onAddToPortfolio(it: WatchlistItem): void {
    const price = it.price !== null && it.price !== undefined ? Number(it.price) : undefined;
    this.entryPrefill.set({
      ticker: it.ticker.toUpperCase(),
      initialPrice: Number.isFinite(price) && (price ?? 0) > 0 ? price : undefined,
    });
    this.entryOpen.set(true);
  }

  onSendToRun(it: WatchlistItem): void {
    this.router.navigate(['/runs/new'], {
      queryParams: { ticker: it.ticker.toUpperCase() },
    });
  }

  onEntryClosed(): void {
    this.entryOpen.set(false);
    this.entryPrefill.set(null);
  }
}
