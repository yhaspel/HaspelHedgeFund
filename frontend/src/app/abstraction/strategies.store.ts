import { Injectable, inject, signal } from '@angular/core';
import { Observable, catchError, finalize, of, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import { apiErrorMessage } from '../core/api/api-error';
import { ExpectedVsRealized } from '../core/models/expected-vs-realized.model';
import {
  CycleDetail,
  CycleEstimate,
  CycleMarkedSnapshot,
  CycleOverrideBody,
  CycleSummary,
  EnrollmentApplyRequest,
  EnrollmentResult,
  Portfolio,
  Position,
  RunNowResponse,
  Strategy,
  Universe,
} from '../core/models/strategy.model';

@Injectable({ providedIn: 'root' })
export class StrategiesStore {
  private readonly api = inject(ApiClient);

  private readonly _strategies = signal<Strategy[]>([]);
  private readonly _universes = signal<Universe[]>([]);
  private readonly _portfolios = signal<Portfolio[]>([]);
  private readonly _cycles = signal<CycleSummary[]>([]);
  private readonly _currentStrategy = signal<Strategy | null>(null);
  private readonly _currentCycle = signal<CycleDetail | null>(null);
  private readonly _positions = signal<Position[]>([]);
  // WAVE 3 — expected-vs-realized for the strategy currently on screen.
  private readonly _evr = signal<ExpectedVsRealized | null>(null);
  private readonly _evrLoading = signal(false);
  private readonly _evrError = signal<string | null>(null);

  readonly strategies = this._strategies.asReadonly();
  readonly universes = this._universes.asReadonly();
  readonly portfolios = this._portfolios.asReadonly();
  readonly cycles = this._cycles.asReadonly();
  readonly currentStrategy = this._currentStrategy.asReadonly();
  readonly currentCycle = this._currentCycle.asReadonly();
  readonly positions = this._positions.asReadonly();
  readonly expectedVsRealized = this._evr.asReadonly();
  readonly evrLoading = this._evrLoading.asReadonly();
  readonly evrError = this._evrError.asReadonly();

  // P10 §D1: the server hides archived (is_active=false) strategies by
  // default; pass includeArchived to fetch everything (the list page does, so
  // it can render the "Archived (N)" chip + client-side toggle).
  list(opts?: { includeArchived?: boolean }): Observable<Strategy[]> {
    const qs = opts?.includeArchived ? '?include_archived=1' : '';
    return this.api.get<Strategy[]>(`/strategies/${qs}`).pipe(
      tap((r) => this._strategies.set(Array.isArray(r) ? r : [])),
    );
  }
  // P10 §D1: archive / unarchive — a plain PATCH on is_active.
  setActive(id: number, active: boolean): Observable<Strategy> {
    return this.api.patch<Strategy>(`/strategies/${id}/`, { is_active: active });
  }
  loadUniverses(): Observable<Universe[]> {
    return this.api.get<Universe[]>('/universes/').pipe(
      tap((r) => this._universes.set(Array.isArray(r) ? r : [])),
    );
  }
  loadPortfolios(): Observable<Portfolio[]> {
    return this.api.get<Portfolio[]>('/portfolios/').pipe(
      tap((r) => this._portfolios.set(Array.isArray(r) ? r : [])),
    );
  }
  createPortfolio(body: { name: string; cash_balance: number }): Observable<Portfolio> {
    return this.api.post<Portfolio>('/portfolios/', body);
  }
  create(body: Partial<Strategy>): Observable<Strategy> {
    return this.api.post<Strategy>('/strategies/', body);
  }
  detail(id: number): Observable<Strategy> {
    return this.api.get<Strategy>(`/strategies/${id}/`).pipe(
      tap((r) => this._currentStrategy.set(r)),
    );
  }
  update(id: number, body: Partial<Strategy>): Observable<Strategy> {
    return this.api.put<Strategy>(`/strategies/${id}/`, body).pipe(
      tap((r) => this._currentStrategy.set(r)),
    );
  }
  estimate(id: number): Observable<CycleEstimate> {
    return this.api.get<CycleEstimate>(`/strategies/${id}/estimate/`);
  }
  /** P4c: re-estimate with a transient tier/model choice from the dispatch
   *  modal (this-run-only — nothing is persisted). */
  estimateWith(id: number, body: CycleOverrideBody): Observable<CycleEstimate> {
    return this.api.post<CycleEstimate>(`/strategies/${id}/estimate/`, body);
  }
  /**
   * POST /strategies/<id>/run-now/.
   *
   * Three success-ish shapes the UI has to tell apart:
   *   • 202 `{task_id, status: "queued"}`     — a new cycle was dispatched
   *   • 200 `{status: "reused", target_id}`   — a same-day DONE cycle already
   *     exists and nothing was dispatched (the page must say so instead of
   *     promising a refresh that will never produce a new row)
   * Failures: 400 (bad / non-today as_of), 409 `{detail, autopilot_run_now}`.
   */
  runNow(
    id: number,
    body: CycleOverrideBody = {},
  ): Observable<RunNowResponse> {
    return this.api.post<RunNowResponse>(`/strategies/${id}/run-now/`, body);
  }
  /** P2l: approve a subset of the persisted screener candidates and dispatch
   *  the council chord. Returns the chord summary + cost estimate. */
  approveCouncil(
    strategyId: number,
    targetId: number,
    body: {
      long_tickers?: string[];
      short_tickers?: string[];
      sector_tickers?: string[];
      pair_keys?: string[];
    },
  ): Observable<{
    target_id: number;
    status: string;
    n_candidates: number;
    run_ids: number[];
    estimate: { est_total_usd: number; cost_ceiling_usd: number; exceeds_ceiling: boolean };
  }> {
    return this.api.post(
      `/strategies/${strategyId}/cycles/${targetId}/approve-council/`,
      body,
    );
  }
  /** P2l: cancel an awaiting_review / running_council / constructing target. */
  rejectCycle(
    strategyId: number,
    targetId: number,
  ): Observable<{ target_id: number; status: string; cancelled_runs: number }> {
    return this.api.post(
      `/strategies/${strategyId}/cycles/${targetId}/reject/`,
      {},
    );
  }
  /** P4 WS-B: rerun a terminal (failed/cancelled) cycle. Dispatches a fresh
   *  cycle for the same as_of date; the old row is stamped superseded. */
  rerunCycle(
    strategyId: number,
    targetId: number,
  ): Observable<{ task_id: string; new_target_pending: boolean; superseded_target_id: number }> {
    return this.api.post(
      `/strategies/${strategyId}/cycles/${targetId}/rerun/`,
      {},
    );
  }
  /** P4 WS-C: delete a strategy with no non-cancelled cycles. */
  deleteStrategy(id: number): Observable<void> {
    return this.api.delete<void>(`/strategies/${id}/`);
  }
  /** P4 WS-E: toggle the per-strategy "auto-enter on cycle done" preference. */
  setAutoEnroll(id: number, value: boolean): Observable<Strategy> {
    return this.api.patch<Strategy>(`/strategies/${id}/`, { auto_enroll_on_done: value }).pipe(
      tap((r) => this._currentStrategy.set(r)),
    );
  }
  /** P4 WS-E: preview the materialization of a done cycle into the book. */
  previewEnrollment(strategyId: number, targetId: number): Observable<EnrollmentResult> {
    return this.api.get<EnrollmentResult>(`/strategies/${strategyId}/enroll/${targetId}/`);
  }
  /** P4 WS-E: apply the enrollment (auto = all rows, manual = approved subset). */
  applyEnrollment(
    strategyId: number, targetId: number, body: EnrollmentApplyRequest,
  ): Observable<EnrollmentResult> {
    return this.api.post<EnrollmentResult>(
      `/strategies/${strategyId}/enroll/${targetId}/`, body,
    );
  }
  listCycles(id: number): Observable<CycleSummary[]> {
    return this.api.get<CycleSummary[]>(`/strategies/${id}/cycles/`).pipe(
      tap((r) => this._cycles.set(Array.isArray(r) ? r : [])),
    );
  }
  cycleDetail(strategyId: number, targetId: number): Observable<CycleDetail> {
    return this.api.get<CycleDetail>(
      `/strategies/${strategyId}/cycles/${targetId}/`,
    ).pipe(tap((r) => this._currentCycle.set(r)));
  }
  /** P3 addendum: force-recompute the cycle's marked snapshot. */
  refreshCycleMark(
    strategyId: number, targetId: number,
  ): Observable<CycleMarkedSnapshot> {
    return this.api.post<CycleMarkedSnapshot>(
      `/strategies/${strategyId}/cycles/${targetId}/refresh-mark/`, {},
    ).pipe(tap((snap) => {
      const cur = this._currentCycle();
      if (cur && cur.id === targetId) {
        this._currentCycle.set({ ...cur, marked_snapshot: snap });
      }
    }));
  }
  /**
   * WAVE 3 — per-cycle realized vs the linked backtest's fold expectation.
   * Failures land on `evrError` (the section renders <hf-error-state>) rather
   * than blowing up the strategy detail page's other subscriptions.
   */
  loadExpectedVsRealized(strategyId: number): Observable<ExpectedVsRealized | null> {
    this._evrLoading.set(true);
    this._evrError.set(null);
    return this.api
      .get<ExpectedVsRealized>(`/strategies/${strategyId}/expected-vs-realized/`)
      .pipe(
        tap((r) => this._evr.set(r)),
        catchError((err: unknown) => {
          this._evr.set(null);
          this._evrError.set(
            apiErrorMessage(err, 'Could not load expected vs realized for this strategy.'),
          );
          return of(null);
        }),
        finalize(() => this._evrLoading.set(false)),
      );
  }

  clearExpectedVsRealized(): void {
    this._evr.set(null);
    this._evrError.set(null);
  }

  positionsFor(portfolioId: number): Observable<Position[]> {
    return this.api.get<Position[]>(`/portfolios/${portfolioId}/positions/`).pipe(
      tap((r) => this._positions.set(Array.isArray(r) ? r : [])),
    );
  }
}
