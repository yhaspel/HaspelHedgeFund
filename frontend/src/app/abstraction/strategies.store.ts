import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  CycleDetail,
  CycleSummary,
  Portfolio,
  Position,
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

  readonly strategies = this._strategies.asReadonly();
  readonly universes = this._universes.asReadonly();
  readonly portfolios = this._portfolios.asReadonly();
  readonly cycles = this._cycles.asReadonly();
  readonly currentStrategy = this._currentStrategy.asReadonly();
  readonly currentCycle = this._currentCycle.asReadonly();
  readonly positions = this._positions.asReadonly();

  list(): Observable<Strategy[]> {
    return this.api.get<Strategy[]>('/strategies/').pipe(
      tap((r) => this._strategies.set(Array.isArray(r) ? r : [])),
    );
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
  runNow(id: number): Observable<{ task_id: string; status: string }> {
    return this.api.post<{ task_id: string; status: string }>(
      `/strategies/${id}/run-now/`, {},
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
  positionsFor(portfolioId: number): Observable<Position[]> {
    return this.api.get<Position[]>(`/portfolios/${portfolioId}/positions/`).pipe(
      tap((r) => this._positions.set(Array.isArray(r) ? r : [])),
    );
  }
}
