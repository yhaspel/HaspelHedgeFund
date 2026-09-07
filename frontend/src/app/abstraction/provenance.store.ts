import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, catchError, finalize, of, tap, throwError } from 'rxjs';

import { ApiClient } from '../core/api/api-client';
import { apiErrorMessage } from '../core/api/api-error';
import {
  PROVENANCE_MAX_TICKERS,
  ProvenanceGlobal,
  ProvenanceRefreshResponse,
  ProvenanceResponse,
  ProvenanceTicker,
} from '../core/models/provenance.model';

/**
 * WAVE 3 — one store behind every `<hf-provenance>` on the page.
 *
 * Per-ticker rows are merged into a single map keyed by SYMBOL so the run
 * page, a position row and the Settings card can each ask for the slice they
 * need without refetching what another surface already has. The global block
 * (macro vintages + provider keys) is a singleton.
 *
 * `refresh()` is optimistic on purpose: the backend only enqueues Celery
 * tasks, so there is nothing to wait for. The queued state is per-ticker-set
 * and clears itself; a 503 (broker down) surfaces its `detail` verbatim.
 */
@Injectable({ providedIn: 'root' })
export class ProvenanceStore {
  private readonly api = inject(ApiClient);

  private readonly _byTicker = signal<Record<string, ProvenanceTicker>>({});
  private readonly _global = signal<ProvenanceGlobal | null>(null);
  private readonly _asOf = signal<string | null>(null);
  private readonly _loading = signal(false);
  private readonly _error = signal<string | null>(null);
  private readonly _queued = signal<string[]>([]);
  private readonly _refreshError = signal<string | null>(null);
  private readonly _refreshing = signal(false);

  readonly byTicker = this._byTicker.asReadonly();
  readonly global = this._global.asReadonly();
  readonly asOf = this._asOf.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly error = this._error.asReadonly();
  /** Tickers whose refresh has been accepted by the broker (optimistic). */
  readonly queued = this._queued.asReadonly();
  readonly refreshError = this._refreshError.asReadonly();
  readonly refreshing = this._refreshing.asReadonly();

  readonly hasGlobal = computed(() => this._global() !== null);

  /** Normalise + de-duplicate + clamp to the backend's own ticker cap. */
  static normalize(tickers: readonly string[]): string[] {
    const out: string[] = [];
    for (const raw of tickers) {
      const t = (raw ?? '').trim().toUpperCase();
      if (!t || out.includes(t)) continue;
      out.push(t);
      if (out.length >= PROVENANCE_MAX_TICKERS) break;
    }
    return out;
  }

  rowsFor(tickers: readonly string[]): ProvenanceTicker[] {
    const map = this._byTicker();
    return ProvenanceStore.normalize(tickers)
      .map((t) => map[t])
      .filter((r): r is ProvenanceTicker => !!r);
  }

  /**
   * Load provenance for `tickers` (empty ⇒ the global block alone).
   * Errors are captured on the store so a panel can render `<hf-error-state>`
   * rather than throwing into an unhandled subscription.
   */
  load(tickers: readonly string[] = []): Observable<ProvenanceResponse | null> {
    const list = ProvenanceStore.normalize(tickers);
    const q = list.length ? `?tickers=${encodeURIComponent(list.join(','))}` : '';
    this._loading.set(true);
    this._error.set(null);
    return this.api.get<ProvenanceResponse>(`/data/provenance/${q}`).pipe(
      tap((res) => {
        this._asOf.set(res.as_of);
        this._global.set(res.global ?? null);
        const incoming = res.tickers ?? {};
        this._byTicker.update((m) => ({ ...m, ...incoming }));
      }),
      catchError((err: unknown) => {
        this._error.set(apiErrorMessage(err, 'Could not load data provenance.'));
        return of(null);
      }),
      finalize(() => this._loading.set(false)),
    );
  }

  /**
   * Queue a data refresh. Optimistic: the tickers go into `queued` before the
   * response lands and stay there — the next `load()` shows whether anything
   * actually moved. A 503 (broker down) clears them and surfaces `detail`.
   */
  refresh(tickers: readonly string[]): Observable<ProvenanceRefreshResponse> {
    const list = ProvenanceStore.normalize(tickers);
    this._refreshError.set(null);
    this._refreshing.set(true);
    this._queued.update((q) => [...new Set([...q, ...list])]);
    return this.api
      .post<ProvenanceRefreshResponse>('/data/provenance/refresh/', { tickers: list })
      .pipe(
        catchError((err: unknown) => {
          this._queued.update((q) => q.filter((t) => !list.includes(t)));
          this._refreshError.set(
            apiErrorMessage(err, 'Could not queue the refresh — try again shortly.'),
          );
          return throwError(() => err);
        }),
        finalize(() => this._refreshing.set(false)),
      );
  }

  clearRefreshError(): void {
    this._refreshError.set(null);
  }
}
