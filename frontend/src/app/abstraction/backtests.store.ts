import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, map, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  BacktestDetail,
  BacktestSummary,
  CreateBacktestRequest,
  DeflationPayload,
  EquityPoint,
  EstimateRequest,
  EstimateResponse,
  RollingSharpePoint,
  StrategyBacktestDefaults,
} from '../core/models/backtest.model';

interface EquityCurveResponse {
  points: EquityPoint[];
  baseline_kind: string;
  // P10 §B2: which benchmark overlays are present + the rolling-Sharpe series.
  benchmarks: string[];
  rolling_sharpe: RollingSharpePoint[];
}

@Injectable({ providedIn: 'root' })
export class BacktestsStore {
  private readonly api = inject(ApiClient);

  private readonly _list = signal<BacktestSummary[]>([]);
  private readonly _listCount = signal(0);
  private readonly _current = signal<BacktestDetail | null>(null);
  private readonly _equity = signal<EquityPoint[]>([]);
  private readonly _equityBenchmarks = signal<string[]>([]);
  private readonly _rollingSharpe = signal<RollingSharpePoint[]>([]);
  private readonly _deflation = signal<DeflationPayload | null>(null);
  private readonly _defaultUniverse = signal<string[]>([]);
  private pollHandle: ReturnType<typeof setTimeout> | null = null;

  readonly list = this._list.asReadonly();
  /** P10 §D4: total rows server-side (the list is paginated at 50). */
  readonly listCount = this._listCount.asReadonly();
  readonly current = this._current.asReadonly();
  readonly equity = this._equity.asReadonly();
  readonly equityBenchmarks = this._equityBenchmarks.asReadonly();
  readonly rollingSharpe = this._rollingSharpe.asReadonly();
  readonly deflation = this._deflation.asReadonly();
  readonly defaultUniverse = this._defaultUniverse.asReadonly();
  readonly isPolling = computed(() => this.pollHandle !== null);

  loadDefaultUniverse(): Observable<{ universe: string[] }> {
    return this.api
      .get<{ universe: string[] }>('/backtests/default-universe/')
      .pipe(tap((r) => this._defaultUniverse.set(r.universe)));
  }

  // P10 §D4: paginated ({count, results}, 50/page); archived rows hidden
  // unless includeArchived.
  listBacktests(opts?: { includeArchived?: boolean; page?: number }):
    Observable<BacktestSummary[]> {
    const params: string[] = [];
    if (opts?.includeArchived) params.push('include_archived=1');
    if (opts?.page && opts.page > 1) params.push(`page=${opts.page}`);
    const qs = params.length ? `?${params.join('&')}` : '';
    return this.api
      .get<BacktestSummary[] | { count: number; results: BacktestSummary[] }>(`/backtests/${qs}`)
      .pipe(
        map((r) => {
          const rows = Array.isArray(r) ? r : (r?.results ?? []);
          this._listCount.set(Array.isArray(r) ? rows.length : (r?.count ?? rows.length));
          this._list.set(rows);
          return rows;
        }),
      );
  }

  // P10 §D4: soft archive/unarchive (the graphs pattern) — done/failed rows
  // can never be deleted, so archive is the declutter verb.
  archiveBacktest(id: number, archived: boolean): Observable<{ id: number; archived_at: string | null }> {
    return this.api.post<{ id: number; archived_at: string | null }>(
      `/backtests/${id}/archive/`, { archived },
    );
  }

  create(body: CreateBacktestRequest): Observable<BacktestSummary> {
    return this.api.post<BacktestSummary>('/backtests/', body);
  }

  estimate(body: EstimateRequest): Observable<EstimateResponse> {
    return this.api.post<EstimateResponse>('/backtests/estimate/', body);
  }

  // phase-09a — the strategy-derived validation-run config the New Backtest page
  // pre-fills when arriving from a fund card (?strategy=<id>).
  strategyDefaults(strategyId: number): Observable<StrategyBacktestDefaults> {
    return this.api.get<StrategyBacktestDefaults>(
      `/strategies/${strategyId}/backtest-defaults/`,
    );
  }

  cancel(id: number): Observable<{ id: number; status: string }> {
    return this.api.post<{ id: number; status: string }>(`/backtests/${id}/cancel/`, {});
  }

  // P4 WS-D: delete a cancelled / aborted / synthetic backtest. done + failed
  // are protected history (the backend returns 409).
  deleteBacktest(id: number): Observable<void> {
    return this.api.delete<void>(`/backtests/${id}/`);
  }

  poll(id: number, intervalMs = 3000): void {
    this.stopPolling();
    const tick = () => {
      this.api.get<BacktestDetail>(`/backtests/${id}/`).subscribe({
        next: (bt) => {
          this._current.set(bt);
          if (bt.status === 'queued' || bt.status === 'running') {
            this.pollHandle = setTimeout(tick, intervalMs);
          } else {
            this.pollHandle = null;
            this.loadEquity(id).subscribe();
            this.loadDeflation(id).subscribe();
          }
        },
        error: () => {
          this.pollHandle = null;
        },
      });
    };
    tick();
  }

  stopPolling(): void {
    if (this.pollHandle) clearTimeout(this.pollHandle);
    this.pollHandle = null;
  }

  loadEquity(id: number): Observable<EquityCurveResponse> {
    return this.api
      .get<EquityCurveResponse>(`/backtests/${id}/equity-curve/`)
      .pipe(tap((r) => {
        this._equity.set(r.points);
        this._equityBenchmarks.set(r.benchmarks ?? []);
        this._rollingSharpe.set(r.rolling_sharpe ?? []);
      }));
  }

  loadDeflation(id: number): Observable<DeflationPayload> {
    return this.api
      .get<DeflationPayload>(`/backtests/${id}/deflation/`)
      .pipe(tap((r) => this._deflation.set(r)));
  }

  compare(a: number, b: number): Observable<{ a: any; b: any }> {
    return this.api.post<{ a: any; b: any }>('/backtests/compare/', {
      backtest_a_id: a, backtest_b_id: b,
    });
  }
}
