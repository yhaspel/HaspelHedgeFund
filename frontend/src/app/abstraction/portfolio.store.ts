/**
 * P3: Manual Book signal store — mirrors the 3-layer (core → abstraction →
 * presentation) pattern used by strategies/runs/backtests.
 *
 * The store fans out a single `loadOverview()` for the dashboard, plus
 * action methods (`openPosition`, `closePosition`, `editPosition`,
 * `adjustCash`, `loadSuggestion`) that update the relevant signals after
 * a mutation, so pages don't need to refetch the whole portfolio.
 */
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  CashAdjustRequest,
  ClosePositionRequest,
  EditPositionRequest,
  LedgerEntry,
  MutationResponse,
  OpenPositionRequest,
  PortfolioOverview,
  PortfolioPreferences,
  PositionSuggestion,
  QuantityMode,
} from '../core/models/portfolio.model';

@Injectable({ providedIn: 'root' })
export class PortfolioStore {
  private readonly api = inject(ApiClient);

  private readonly _overview = signal<PortfolioOverview | null>(null);
  private readonly _ledger = signal<LedgerEntry[]>([]);
  private readonly _suggestion = signal<PositionSuggestion | null>(null);
  private readonly _suggestionLoading = signal(false);
  private readonly _busy = signal(false);
  private readonly _error = signal<string | null>(null);
  private readonly _preferences = signal<PortfolioPreferences | null>(null);
  private readonly _refreshing = signal(false);

  readonly overview = this._overview.asReadonly();
  readonly ledger = this._ledger.asReadonly();
  readonly suggestion = this._suggestion.asReadonly();
  readonly suggestionLoading = this._suggestionLoading.asReadonly();
  readonly busy = this._busy.asReadonly();
  readonly error = this._error.asReadonly();
  readonly preferences = this._preferences.asReadonly();
  readonly refreshing = this._refreshing.asReadonly();

  setError(message: string | null): void {
    this._error.set(message);
  }

  resetSuggestion(): void {
    this._suggestion.set(null);
  }

  loadOverview(): Observable<PortfolioOverview> {
    return this.api.get<PortfolioOverview>('/portfolio/').pipe(
      tap((r) => {
        this._overview.set(r);
        if (r.preferences) this._preferences.set(r.preferences);
      }),
    );
  }

  loadPreferences(): Observable<PortfolioPreferences> {
    return this.api
      .get<PortfolioPreferences>('/portfolio/preferences/')
      .pipe(tap((r) => this._preferences.set(r)));
  }

  savePreferences(body: Partial<PortfolioPreferences>): Observable<PortfolioPreferences> {
    return this.api
      .put<PortfolioPreferences>('/portfolio/preferences/', body)
      .pipe(tap((r) => this._preferences.set(r)));
  }

  refreshMarks(): Observable<PortfolioOverview> {
    this._refreshing.set(true);
    return this.api.post<PortfolioOverview>('/portfolio/refresh-marks/', {}).pipe(
      tap({
        next: (r) => {
          this._overview.set(r);
          if (r.preferences) this._preferences.set(r.preferences);
          this._refreshing.set(false);
        },
        error: () => this._refreshing.set(false),
      }),
    );
  }

  loadLedger(): Observable<LedgerEntry[]> {
    return this.api
      .get<LedgerEntry[]>('/portfolio/ledger/')
      .pipe(tap((r) => this._ledger.set(Array.isArray(r) ? r : [])));
  }

  loadSuggestion(
    runId: number,
    decisionId: number,
    quantityMode: QuantityMode = 'whole',
  ): Observable<PositionSuggestion> {
    this._suggestionLoading.set(true);
    return this.api
      .get<PositionSuggestion>(
        `/portfolio/position-suggestion/?run=${runId}&decision=${decisionId}&quantity_mode=${quantityMode}`,
      )
      .pipe(
        tap({
          next: (r) => {
            this._suggestion.set(r);
            this._suggestionLoading.set(false);
          },
          error: () => this._suggestionLoading.set(false),
        }),
      );
  }

  openPosition(body: OpenPositionRequest): Observable<MutationResponse> {
    this._busy.set(true);
    return this.api.post<MutationResponse>('/portfolio/positions/', body).pipe(
      tap({
        next: (r) => {
          this._overview.set(r.portfolio);
          this._ledger.update((rows) => [r.ledger_entry, ...rows]);
          this._busy.set(false);
        },
        error: () => this._busy.set(false),
      }),
    );
  }

  closePosition(
    positionId: number,
    body: ClosePositionRequest,
  ): Observable<MutationResponse & { realized_pnl: string }> {
    this._busy.set(true);
    return this.api
      .post<MutationResponse & { realized_pnl: string }>(
        `/portfolio/positions/${positionId}/close/`,
        body,
      )
      .pipe(
        tap({
          next: (r) => {
            this._overview.set(r.portfolio);
            this._ledger.update((rows) => [r.ledger_entry, ...rows]);
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  editPosition(
    positionId: number,
    body: EditPositionRequest,
  ): Observable<MutationResponse> {
    this._busy.set(true);
    return this.api
      .patch<MutationResponse>(`/portfolio/positions/${positionId}/`, body)
      .pipe(
        tap({
          next: (r) => {
            this._overview.set(r.portfolio);
            this._ledger.update((rows) => [r.ledger_entry, ...rows]);
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  adjustCash(body: CashAdjustRequest): Observable<MutationResponse> {
    this._busy.set(true);
    return this.api.post<MutationResponse>('/portfolio/cash/', body).pipe(
      tap({
        next: (r) => {
          this._overview.set(r.portfolio);
          this._ledger.update((rows) => [r.ledger_entry, ...rows]);
          this._busy.set(false);
        },
        error: () => this._busy.set(false),
      }),
    );
  }
}
