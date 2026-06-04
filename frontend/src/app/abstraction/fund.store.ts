import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  Autopilot,
  AutopilotResponse,
  AutopilotRunRow,
  ExecutedBook,
  FundOverview,
} from '../core/models/autopilot.model';

// P7 — autopilot + fund store (signals, singleton). Mirrors StrategiesStore.
@Injectable({ providedIn: 'root' })
export class FundStore {
  private readonly api = inject(ApiClient);

  private readonly _fund = signal<FundOverview | null>(null);
  private readonly _autopilot = signal<Autopilot | null>(null);
  private readonly _history = signal<AutopilotRunRow[]>([]);
  private readonly _executed = signal<ExecutedBook | null>(null);

  readonly fund = this._fund.asReadonly();
  readonly autopilot = this._autopilot.asReadonly();
  readonly history = this._history.asReadonly();
  readonly executed = this._executed.asReadonly();

  // --- fund ---
  loadFund(): Observable<FundOverview> {
    return this.api.get<FundOverview>('/fund/').pipe(tap((r) => this._fund.set(r ?? null)));
  }
  haltFund(): Observable<unknown> {
    return this.api.post('/fund/halt/', {}).pipe(tap(() => this.loadFund().subscribe()));
  }
  resumeFund(): Observable<unknown> {
    return this.api.post('/fund/resume/', {}).pipe(tap(() => this.loadFund().subscribe()));
  }

  // --- autopilot (per strategy) ---
  loadAutopilot(strategyId: number): Observable<AutopilotResponse> {
    return this.api
      .get<AutopilotResponse>(`/strategies/${strategyId}/autopilot/`)
      .pipe(tap((r) => this._autopilot.set(r?.autopilot ?? null)));
  }
  saveAutopilot(strategyId: number, body: Partial<Autopilot>): Observable<AutopilotResponse> {
    return this.api
      .put<AutopilotResponse>(`/strategies/${strategyId}/autopilot/`, body)
      .pipe(tap((r) => this._autopilot.set(r?.autopilot ?? null)));
  }
  enable(strategyId: number): Observable<AutopilotResponse> {
    return this.api
      .post<AutopilotResponse>(`/strategies/${strategyId}/autopilot/enable/`, {})
      .pipe(tap((r) => this._autopilot.set(r?.autopilot ?? null)));
  }
  disable(strategyId: number): Observable<AutopilotResponse> {
    return this.api
      .post<AutopilotResponse>(`/strategies/${strategyId}/autopilot/disable/`, {})
      .pipe(tap((r) => this._autopilot.set(r?.autopilot ?? null)));
  }
  resume(strategyId: number): Observable<AutopilotResponse> {
    return this.api
      .post<AutopilotResponse>(`/strategies/${strategyId}/autopilot/resume/`, {})
      .pipe(tap((r) => this._autopilot.set(r?.autopilot ?? null)));
  }
  runNow(strategyId: number): Observable<unknown> {
    return this.api.post(`/strategies/${strategyId}/autopilot/run-now/`, {});
  }
  loadHistory(strategyId: number): Observable<{ runs: AutopilotRunRow[] }> {
    return this.api
      .get<{ runs: AutopilotRunRow[] }>(`/strategies/${strategyId}/autopilot/history/`)
      .pipe(tap((r) => this._history.set(r?.runs ?? [])));
  }
  loadExecuted(strategyId: number): Observable<ExecutedBook> {
    return this.api
      .get<ExecutedBook>(`/strategies/${strategyId}/executed/`)
      .pipe(tap((r) => this._executed.set(r ?? null)));
  }
}
