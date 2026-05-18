import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  BacktestDetail,
  BacktestSummary,
  CreateBacktestRequest,
  DeflationPayload,
  EquityPoint,
  EstimateRequest,
  EstimateResponse,
} from '../core/models/backtest.model';

@Injectable({ providedIn: 'root' })
export class BacktestsStore {
  private readonly api = inject(ApiClient);

  private readonly _list = signal<BacktestSummary[]>([]);
  private readonly _current = signal<BacktestDetail | null>(null);
  private readonly _equity = signal<EquityPoint[]>([]);
  private readonly _deflation = signal<DeflationPayload | null>(null);
  private readonly _defaultUniverse = signal<string[]>([]);
  private pollHandle: ReturnType<typeof setTimeout> | null = null;

  readonly list = this._list.asReadonly();
  readonly current = this._current.asReadonly();
  readonly equity = this._equity.asReadonly();
  readonly deflation = this._deflation.asReadonly();
  readonly defaultUniverse = this._defaultUniverse.asReadonly();
  readonly isPolling = computed(() => this.pollHandle !== null);

  loadDefaultUniverse(): Observable<{ universe: string[] }> {
    return this.api
      .get<{ universe: string[] }>('/backtests/default-universe/')
      .pipe(tap((r) => this._defaultUniverse.set(r.universe)));
  }

  listBacktests(): Observable<BacktestSummary[]> {
    return this.api
      .get<BacktestSummary[]>('/backtests/')
      .pipe(tap((r) => this._list.set(Array.isArray(r) ? r : [])));
  }

  create(body: CreateBacktestRequest): Observable<BacktestSummary> {
    return this.api.post<BacktestSummary>('/backtests/', body);
  }

  estimate(body: EstimateRequest): Observable<EstimateResponse> {
    return this.api.post<EstimateResponse>('/backtests/estimate/', body);
  }

  cancel(id: number): Observable<{ id: number; status: string }> {
    return this.api.post<{ id: number; status: string }>(`/backtests/${id}/cancel/`, {});
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

  loadEquity(id: number): Observable<{ points: EquityPoint[]; baseline_kind: string }> {
    return this.api
      .get<{ points: EquityPoint[]; baseline_kind: string }>(`/backtests/${id}/equity-curve/`)
      .pipe(tap((r) => this._equity.set(r.points)));
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
