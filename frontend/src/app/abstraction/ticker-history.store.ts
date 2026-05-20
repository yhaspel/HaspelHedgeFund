import { Injectable, inject, signal } from '@angular/core';
import { Observable, of, shareReplay, tap } from 'rxjs';
import { map, catchError } from 'rxjs/operators';
import { ApiClient } from '../core/api/api-client';

interface SparklineResponse {
  ticker: string;
  as_of: string;
  bars: { date: string; close: number }[];
}

interface CacheEntry {
  fetchedAt: number;
  closes: number[];
  inFlight?: Observable<number[]>;
}

const TTL_MS = 15 * 60 * 1000;

@Injectable({ providedIn: 'root' })
export class TickerHistoryStore {
  private readonly api = inject(ApiClient);
  private readonly cache = new Map<string, CacheEntry>();
  readonly _bump = signal(0);

  closes(ticker: string): number[] | null {
    const key = ticker.toUpperCase();
    const entry = this.cache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.fetchedAt > TTL_MS) {
      this.cache.delete(key);
      return null;
    }
    return entry.closes;
  }

  fetch(ticker: string, days = 60): Observable<number[]> {
    const key = ticker.toUpperCase();
    const existing = this.cache.get(key);
    if (existing && Date.now() - existing.fetchedAt < TTL_MS) {
      if (existing.inFlight) return existing.inFlight;
      return of(existing.closes);
    }
    const req$ = this.api
      .get<SparklineResponse>(`/tickers/${encodeURIComponent(key)}/sparkline/?days=${days}`)
      .pipe(
        map((r) => r.bars.map((b) => b.close)),
        catchError(() => of<number[]>([])),
        tap((closes) => {
          this.cache.set(key, { fetchedAt: Date.now(), closes });
          this._bump.update((n) => n + 1);
        }),
        shareReplay(1),
      );
    this.cache.set(key, { fetchedAt: Date.now(), closes: [], inFlight: req$ });
    return req$;
  }
}
