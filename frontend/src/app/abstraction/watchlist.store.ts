/**
 * Shared root-level watchlist store (P3-prereq-5 WS-E).
 *
 * The watchlist data model + API live in apps.screener (see the planning §12);
 * this store gives every UI surface (the Screener tab, the new /watchlist page,
 * the Dashboard card, the Profile card) a single source of truth without
 * scattering the API calls. ScreenerStore delegates to this so its existing
 * star-toggle keeps working unchanged.
 */
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import { ApiClient } from '../core/api/api-client';
import {
  WatchlistItem,
  WatchlistResponse,
} from '../core/models/screener.model';

@Injectable({ providedIn: 'root' })
export class WatchlistStore {
  private readonly api = inject(ApiClient);

  private readonly _items = signal<WatchlistItem[]>([]);
  private readonly _busy = signal(false);
  private readonly _loaded = signal(false);
  private readonly _error = signal<string | null>(null);

  readonly items = this._items.asReadonly();
  readonly busy = this._busy.asReadonly();
  readonly loaded = this._loaded.asReadonly();
  readonly error = this._error.asReadonly();

  readonly tickers = computed(
    () => new Set(this._items().map((i) => i.ticker.toUpperCase())),
  );

  load(): Observable<WatchlistResponse> {
    this._busy.set(true);
    return this.api.get<WatchlistResponse>('/screener/watchlist/').pipe(
      tap({
        next: (r) => {
          this._items.set(r.items);
          this._loaded.set(true);
          this._busy.set(false);
        },
        error: (err) => {
          this._error.set(
            err?.error?.detail ?? err?.message ?? 'Failed to load watchlist.',
          );
          this._busy.set(false);
        },
      }),
    );
  }

  add(ticker: string, note = ''): Observable<WatchlistItem> {
    const upper = ticker.trim().toUpperCase();
    return this.api
      .post<WatchlistItem>('/screener/watchlist/', { ticker: upper, note })
      .pipe(
        tap((item) => {
          if (!this._items().some((i) => i.ticker.toUpperCase() === upper)) {
            this._items.update((rows) => [item, ...rows]);
          }
        }),
      );
  }

  remove(ticker: string): Observable<void> {
    const upper = ticker.trim().toUpperCase();
    return this.api.delete<void>(`/screener/watchlist/${upper}/`).pipe(
      tap(() => {
        this._items.update((rows) =>
          rows.filter((r) => r.ticker.toUpperCase() !== upper),
        );
      }),
    );
  }

  hasTicker(ticker: string): boolean {
    return this.tickers().has(ticker.toUpperCase());
  }

  /** Used by ScreenerStore when the user adds via the results-table star. */
  upsertItem(item: WatchlistItem): void {
    const upper = item.ticker.toUpperCase();
    if (!this._items().some((i) => i.ticker.toUpperCase() === upper)) {
      this._items.update((rows) => [item, ...rows]);
    }
  }

  /** Used by ScreenerStore when a row is unstarred. */
  removeLocal(ticker: string): void {
    const upper = ticker.toUpperCase();
    this._items.update((rows) =>
      rows.filter((r) => r.ticker.toUpperCase() !== upper),
    );
  }

  setError(msg: string | null): void {
    this._error.set(msg);
  }
}
