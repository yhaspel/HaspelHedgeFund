import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  Autopilot,
  AutopilotResponse,
  AutopilotRunRow,
  ExecutedBook,
  FundAccountOption,
  FundCandidate,
  FundComposite,
  FundHistory,
  FundMemberInput,
  FundMembersChange,
  FundOverview,
} from '../core/models/autopilot.model';

// P7 — autopilot + fund store (signals, singleton). Mirrors StrategiesStore.
// P14 — also the Fund tab's management surface: the shared paper account, the
// member roster + allocations, reset (fresh start) and flatten.
@Injectable({ providedIn: 'root' })
export class FundStore {
  private readonly api = inject(ApiClient);

  private readonly _fund = signal<FundOverview | null>(null);
  private readonly _composite = signal<FundComposite | null>(null);
  private readonly _navHistory = signal<FundHistory | null>(null);
  private readonly _autopilot = signal<Autopilot | null>(null);
  private readonly _history = signal<AutopilotRunRow[]>([]);
  private readonly _executed = signal<ExecutedBook | null>(null);
  private readonly _candidates = signal<FundCandidate[]>([]);
  private readonly _accounts = signal<FundAccountOption[]>([]);

  readonly fund = this._fund.asReadonly();
  readonly composite = this._composite.asReadonly();
  readonly navHistory = this._navHistory.asReadonly();
  readonly autopilot = this._autopilot.asReadonly();
  readonly history = this._history.asReadonly();
  readonly executed = this._executed.asReadonly();
  readonly candidates = this._candidates.asReadonly();
  readonly accounts = this._accounts.asReadonly();

  // The GET returns `{fund: null}` before a fund exists — normalize to null so
  // the page renders the set-up flow instead of a half-empty overview.
  private static overview(r: unknown): FundOverview | null {
    if (!r || typeof r !== 'object') return null;
    if ('fund' in (r as Record<string, unknown>) && (r as { fund: unknown }).fund === null) {
      return null;
    }
    return r as FundOverview;
  }

  // --- fund ---
  loadFund(): Observable<FundOverview> {
    return this.api
      .get<FundOverview>('/fund/')
      .pipe(tap((r) => this._fund.set(FundStore.overview(r))));
  }
  // P14 — create the fund and/or set its name, DD halt and the ONE shared paper account.
  configureFund(body: {
    name?: string;
    fund_dd_halt_pct?: string | number;
    broker_account_id?: number;
  }): Observable<FundOverview> {
    return this.api
      .put<FundOverview>('/fund/', body)
      .pipe(tap((r) => this._fund.set(FundStore.overview(r))));
  }
  // P14 — replace the roster + allocations (must total 100%). A member that
  // still holds positions is refused (409 + blocking) unless forceFlatten.
  setMembers(
    members: FundMemberInput[],
    forceFlatten = false,
  ): Observable<FundOverview & { changes: FundMembersChange }> {
    return this.api
      .put<FundOverview & { changes: FundMembersChange }>('/fund/members/', {
        members,
        force_flatten: forceFlatten,
      })
      .pipe(tap((r) => this._fund.set(FundStore.overview(r))));
  }
  loadCandidates(): Observable<{ strategies: FundCandidate[] }> {
    return this.api
      .get<{ strategies: FundCandidate[] }>('/fund/candidates/')
      .pipe(tap((r) => this._candidates.set(r?.strategies ?? [])));
  }
  loadAccounts(): Observable<{ accounts: FundAccountOption[] }> {
    return this.api
      .get<{ accounts: FundAccountOption[] }>('/fund/accounts/')
      .pipe(tap((r) => this._accounts.set(r?.accounts ?? [])));
  }
  // P14 — fresh start: split the account's cash by allocation, re-arm breakers.
  resetFund(): Observable<FundOverview> {
    return this.api
      .post<FundOverview>('/fund/reset/', {})
      .pipe(tap((r) => this._fund.set(FundStore.overview(r))));
  }
  // P14 — queue closing orders for every position in the shared account.
  flattenFund(): Observable<FundOverview> {
    return this.api
      .post<FundOverview>('/fund/flatten/', {})
      .pipe(tap((r) => this._fund.set(FundStore.overview(r))));
  }
  // P10 §B5 / P11 A3 — the validated composite (pods' stitched OOS curves vs
  // SPY/QQQ-TR), optionally re-levered / clipped to the post-GFC sub-period.
  loadComposite(opts?: {
    weights?: string;
    leverage?: number;
    subPeriod?: 'full' | 'post_gfc';
  }): Observable<FundComposite> {
    const params = new URLSearchParams();
    if (opts?.weights) params.set('weights', opts.weights);
    if (opts?.leverage && opts.leverage !== 1) params.set('leverage', String(opts.leverage));
    if (opts?.subPeriod === 'post_gfc') params.set('sub_period', 'post_gfc');
    const qs = params.toString();
    const q = qs ? `?${qs}` : '';
    return this.api
      .get<FundComposite>(`/fund/composite/${q}`)
      .pipe(tap((r) => this._composite.set(r ?? null)));
  }
  // P10 §C2 — persisted NAV history (per-account + aggregate + SPY/QQQ, TWR).
  loadNavHistory(days?: number): Observable<FundHistory> {
    const q = days ? `?days=${days}` : '';
    return this.api
      .get<FundHistory>(`/fund/history/${q}`)
      .pipe(tap((r) => this._navHistory.set(r ?? null)));
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
