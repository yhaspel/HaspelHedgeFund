import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  RegimeBatchResponse,
  RegimeHistoryResponse,
  RegimeSnapshot,
  RegimeSnapshotResponse,
} from '../core/models/regime.model';

@Injectable({ providedIn: 'root' })
export class RegimeStore {
  private readonly api = inject(ApiClient);

  private readonly _byTicker = signal<Record<string, RegimeSnapshot | null>>({});
  private readonly _history = signal<RegimeHistoryResponse | null>(null);

  readonly byTicker = this._byTicker.asReadonly();
  readonly history = this._history.asReadonly();

  loadOne(ticker: string, asOf?: string): Observable<RegimeSnapshotResponse> {
    const q = asOf ? `?as_of=${asOf}` : '';
    return this.api
      .get<RegimeSnapshotResponse>(`/macro/regime/${ticker}/${q}`)
      .pipe(tap((r) => this._mergeOne(r.ticker, r.snapshot)));
  }

  loadBatch(tickers: string[], asOf?: string): Observable<RegimeBatchResponse> {
    const params = new URLSearchParams();
    params.set('tickers', tickers.join(','));
    if (asOf) params.set('as_of', asOf);
    return this.api
      .get<RegimeBatchResponse>(`/macro/regime/batch/?${params.toString()}`)
      .pipe(tap((r) => {
        for (const item of r.items) {
          this._mergeOne(item.ticker, item.snapshot);
        }
      }));
  }

  loadHistory(
    ticker: string, from?: string, to?: string
  ): Observable<RegimeHistoryResponse> {
    const params = new URLSearchParams();
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    const q = params.toString() ? `?${params.toString()}` : '';
    return this.api
      .get<RegimeHistoryResponse>(`/macro/regime/${ticker}/history/${q}`)
      .pipe(tap((h) => this._history.set(h)));
  }

  private _mergeOne(ticker: string, snap: RegimeSnapshot | null): void {
    this._byTicker.update((m) => ({ ...m, [ticker.toUpperCase()]: snap }));
  }
}
