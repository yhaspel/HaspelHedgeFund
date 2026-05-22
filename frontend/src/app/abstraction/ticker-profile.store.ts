import { Injectable, inject, signal } from '@angular/core';
import { Observable, of, shareReplay, tap } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { ApiClient } from '../core/api/api-client';
import {
  TickerIdentity,
  TickerProfile,
  TickerProfileBatchResponse,
} from '../core/models/ticker.model';

// WS-2: live quote-derived metrics expire after ~30 min; identity-only entries
// (Name column) live effectively forever (re-fetched on hard reload).
const PROFILE_TTL_MS = 30 * 60 * 1000;
const IDENTITY_TTL_MS = 24 * 60 * 60 * 1000;

interface ProfileCacheEntry {
  fetchedAt: number;
  profile: TickerProfile | null;
  inFlight?: Observable<TickerProfile | null>;
}

interface IdentityCacheEntry {
  fetchedAt: number;
  identity: TickerIdentity | null;
}

@Injectable({ providedIn: 'root' })
export class TickerProfileStore {
  private readonly api = inject(ApiClient);
  private readonly profiles = new Map<string, ProfileCacheEntry>();
  private readonly identities = new Map<string, IdentityCacheEntry>();
  private readonly inFlightBatches = new Map<string, Observable<Record<string, TickerIdentity>>>();
  readonly _bump = signal(0);

  /** Synchronous read of cached identity — used by table cells. */
  name(ticker: string): string | null {
    const key = ticker.toUpperCase();
    const entry = this.identities.get(key);
    if (!entry) return null;
    if (Date.now() - entry.fetchedAt > IDENTITY_TTL_MS) {
      this.identities.delete(key);
      return null;
    }
    return entry.identity?.name ?? '';
  }

  /** Synchronous read of cached full profile — used by popover content. */
  profile(ticker: string): TickerProfile | null {
    const key = ticker.toUpperCase();
    const entry = this.profiles.get(key);
    if (!entry) return null;
    if (Date.now() - entry.fetchedAt > PROFILE_TTL_MS) {
      this.profiles.delete(key);
      return null;
    }
    return entry.profile;
  }

  /** Lazy single fetch — used by the hf-ticker popover on first hover/focus. */
  fetchProfile(ticker: string): Observable<TickerProfile | null> {
    const key = ticker.toUpperCase();
    const existing = this.profiles.get(key);
    if (existing && Date.now() - existing.fetchedAt < PROFILE_TTL_MS) {
      if (existing.inFlight) return existing.inFlight;
      return of(existing.profile);
    }
    const req$ = this.api
      .get<TickerProfile>(`/tickers/${encodeURIComponent(key)}/profile/`)
      .pipe(
        catchError(() => of<TickerProfile | null>(null)),
        tap((profile) => {
          this.profiles.set(key, { fetchedAt: Date.now(), profile });
          if (profile && profile.name) {
            this.identities.set(key, {
              fetchedAt: Date.now(),
              identity: {
                name: profile.name,
                exchange: profile.exchange,
                sector: profile.sector,
              },
            });
          }
          this._bump.update((n) => n + 1);
        }),
        shareReplay(1),
      );
    this.profiles.set(key, { fetchedAt: Date.now(), profile: null, inFlight: req$ });
    return req$;
  }

  /** Batch identity-only fetch — used by table pages to populate the Name
   *  column with one round-trip. Tickers already in cache are skipped.
   *  Returns immediately if all tickers are already resolved.
   */
  fetchNames(tickers: string[]): Observable<Record<string, TickerIdentity>> {
    const keys = [...new Set(tickers.map((t) => t.toUpperCase()).filter(Boolean))];
    const projected = (): Record<string, TickerIdentity> => {
      const out: Record<string, TickerIdentity> = {};
      for (const k of keys) {
        const id = this.identities.get(k)?.identity;
        if (id) out[k] = id;
      }
      return out;
    };
    const missing = keys.filter((k) => {
      const entry = this.identities.get(k);
      return !entry || Date.now() - entry.fetchedAt > IDENTITY_TTL_MS;
    });
    if (!missing.length) return of(projected());

    const batchKey = missing.slice().sort().join(',');
    const inFlight = this.inFlightBatches.get(batchKey);
    if (inFlight) return inFlight;

    const url = `/tickers/profiles/?symbols=${encodeURIComponent(missing.join(','))}`;
    const req$ = this.api.get<TickerProfileBatchResponse>(url).pipe(
      catchError(() => of<TickerProfileBatchResponse>({ profiles: {} })),
      tap((resp) => {
        const now = Date.now();
        for (const sym of missing) {
          const id = resp.profiles?.[sym] ?? { name: '', exchange: '', sector: '' };
          this.identities.set(sym, { fetchedAt: now, identity: id });
        }
        this.inFlightBatches.delete(batchKey);
        this._bump.update((n) => n + 1);
      }),
      map(() => projected()),
      shareReplay(1),
    );
    this.inFlightBatches.set(batchKey, req$);
    return req$;
  }
}
