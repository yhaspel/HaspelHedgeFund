import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, of, shareReplay, tap } from 'rxjs';
import { map } from 'rxjs/operators';

import { ApiClient } from '../core/api/api-client';
import {
  AssetClass,
  Preset,
  PresetsResponse,
  ScreenCriterion,
  ScreenRequest,
  ScreenResult,
  SavedScreen,
  ScreenerField,
  ScreenerFieldsResponse,
  WatchlistItem,
  WatchlistResponse,
} from '../core/models/screener.model';
import { WatchlistStore } from './watchlist.store';

const CATALOG_TTL_MS = 30 * 60 * 1000;

@Injectable({ providedIn: 'root' })
export class ScreenerStore {
  private readonly api = inject(ApiClient);
  private readonly watchlistStore = inject(WatchlistStore);

  private readonly _fields = signal<ScreenerField[]>([]);
  private readonly _capabilities = signal<string[]>([]);
  private readonly _presets = signal<Preset[]>([]);
  private readonly _activeAssetClass = signal<AssetClass>('equity');
  private readonly _activeCriteria = signal<Record<string, ScreenCriterion>>({});
  private readonly _activeSort = signal<{ field: string; dir: 'asc' | 'desc' }>({
    field: 'market_cap',
    dir: 'desc',
  });
  private readonly _activePresetId = signal<string>('');
  private readonly _result = signal<ScreenResult | null>(null);
  private readonly _running = signal(false);
  private readonly _error = signal<string | null>(null);
  private readonly _saved = signal<SavedScreen[]>([]);

  private _fieldsFetchedAt = 0;
  private _fieldsInFlight: Observable<ScreenerFieldsResponse> | null = null;
  private _presetsFetchedAt = 0;
  private _presetsInFlight: Observable<PresetsResponse> | null = null;

  readonly fields = this._fields.asReadonly();
  readonly capabilities = this._capabilities.asReadonly();
  readonly presets = this._presets.asReadonly();
  readonly activeAssetClass = this._activeAssetClass.asReadonly();
  readonly activeCriteria = this._activeCriteria.asReadonly();
  readonly activeSort = this._activeSort.asReadonly();
  readonly activePresetId = this._activePresetId.asReadonly();
  readonly result = this._result.asReadonly();
  readonly running = this._running.asReadonly();
  readonly error = this._error.asReadonly();
  readonly saved = this._saved.asReadonly();

  // Watchlist signals delegated to WatchlistStore so every surface
  // (Screener tab, /watchlist page, Dashboard card, Profile card) sees the
  // same data. Public API is unchanged from the pre-extraction shape.
  readonly watchlist = this.watchlistStore.items;
  readonly watchlistBusy = this.watchlistStore.busy;
  readonly watchlistTickers = this.watchlistStore.tickers;

  readonly fieldsByGroup = computed(() => {
    const groups: Record<string, ScreenerField[]> = {};
    for (const f of this._fields()) {
      if (!groups[f.group]) groups[f.group] = [];
      groups[f.group].push(f);
    }
    return groups;
  });

  setError(message: string | null): void {
    this._error.set(message);
  }

  setAssetClass(ac: AssetClass): void {
    this._activeAssetClass.set(ac);
  }

  setCriterion(fieldId: string, spec: ScreenCriterion | null): void {
    const next = { ...this._activeCriteria() };
    if (spec === null) {
      delete next[fieldId];
    } else {
      next[fieldId] = spec;
    }
    this._activeCriteria.set(next);
  }

  setSort(field: string, dir: 'asc' | 'desc'): void {
    this._activeSort.set({ field, dir });
  }

  resetCriteria(): void {
    this._activeCriteria.set({});
    this._activePresetId.set('');
  }

  applyPreset(preset: Preset): void {
    this._activePresetId.set(preset.id);
    this._activeAssetClass.set(preset.asset_class);
    this._activeCriteria.set({ ...preset.filters });
    this._activeSort.set({ ...preset.sort });
  }

  loadFields(): Observable<ScreenerFieldsResponse> {
    if (
      this._fields().length &&
      Date.now() - this._fieldsFetchedAt < CATALOG_TTL_MS
    ) {
      return of({ fields: this._fields(), capabilities: this._capabilities() });
    }
    if (this._fieldsInFlight) return this._fieldsInFlight;
    const req$ = this.api.get<ScreenerFieldsResponse>('/screener/fields/').pipe(
      tap((r) => {
        this._fields.set(r.fields);
        this._capabilities.set(r.capabilities);
        this._fieldsFetchedAt = Date.now();
        this._fieldsInFlight = null;
      }),
      shareReplay(1),
    );
    this._fieldsInFlight = req$;
    return req$;
  }

  loadPresets(): Observable<PresetsResponse> {
    if (
      this._presets().length &&
      Date.now() - this._presetsFetchedAt < CATALOG_TTL_MS
    ) {
      return of({ presets: this._presets() });
    }
    if (this._presetsInFlight) return this._presetsInFlight;
    const req$ = this.api.get<PresetsResponse>('/screener/presets/').pipe(
      tap((r) => {
        this._presets.set(r.presets);
        this._presetsFetchedAt = Date.now();
        this._presetsInFlight = null;
      }),
      shareReplay(1),
    );
    this._presetsInFlight = req$;
    return req$;
  }

  runScreen(): Observable<ScreenResult> {
    const body: ScreenRequest = {
      asset_class: this._activeAssetClass(),
      criteria: this._activeCriteria(),
      sort: this._activeSort(),
      limit: 200,
      preset_id: this._activePresetId() || undefined,
    };
    this._running.set(true);
    this._error.set(null);
    return this.api.post<ScreenResult>('/screener/run/', body).pipe(
      tap({
        next: (r) => {
          this._result.set(r);
          this._running.set(false);
        },
        error: (e) => {
          const detail = e?.error?.detail || e?.message || 'Run failed';
          this._error.set(detail);
          this._running.set(false);
        },
      }),
    );
  }

  loadSaved(): Observable<SavedScreen[]> {
    return this.api.get<SavedScreen[]>('/screener/saved/').pipe(
      tap((rows) => this._saved.set(rows)),
    );
  }

  saveScreen(name: string): Observable<SavedScreen> {
    const body = {
      name,
      asset_class: this._activeAssetClass(),
      filters: this._activeCriteria(),
      sort: this._activeSort(),
      based_on: this._activePresetId(),
    };
    return this.api.post<SavedScreen>('/screener/saved/', body).pipe(
      tap((row) => this._saved.update((rows) => [...rows, row])),
    );
  }

  deleteSaved(id: number): Observable<void> {
    return this.api.delete<void>(`/screener/saved/${id}/`).pipe(
      tap(() =>
        this._saved.update((rows) => rows.filter((r) => r.id !== id)),
      ),
    );
  }

  loadSavedInto(screen: SavedScreen): void {
    this._activeAssetClass.set(screen.asset_class);
    this._activeCriteria.set({ ...screen.filters });
    this._activePresetId.set(screen.based_on || '');
    this._activeSort.set({ ...screen.sort });
  }

  loadWatchlist(): Observable<WatchlistResponse> {
    return this.watchlistStore.load();
  }

  addToWatchlist(ticker: string, note = ''): Observable<WatchlistItem> {
    return this.watchlistStore.add(ticker, note).pipe(
      tap((item) => {
        const upper = item.ticker.toUpperCase();
        // Reflect in any currently-displayed result rows.
        const res = this._result();
        if (res) {
          this._result.set({
            ...res,
            rows: res.rows.map((r) =>
              r.ticker.toUpperCase() === upper
                ? { ...r, in_watchlist: true }
                : r,
            ),
          });
        }
      }),
    );
  }

  removeFromWatchlist(ticker: string): Observable<void> {
    return this.watchlistStore.remove(ticker).pipe(
      tap(() => {
        const upper = ticker.toUpperCase();
        const res = this._result();
        if (res) {
          this._result.set({
            ...res,
            rows: res.rows.map((r) =>
              r.ticker.toUpperCase() === upper
                ? { ...r, in_watchlist: false }
                : r,
            ),
          });
        }
      }),
      map(() => undefined),
    );
  }
}
