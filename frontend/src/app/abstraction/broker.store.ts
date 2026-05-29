/**
 * P3a-1 broker store. Signals-based, following the same Core → Abstraction
 * → Presentation pattern as PortfolioStore. Exposes the lists/overview the
 * UI needs and the action methods (`createAccount`, `createDraftOrder`,
 * `confirmOrder`, `cancelOrder`, `syncAccount`, `acceptDisclaimer`) that
 * mutate state and update the signals after the round-trip.
 */
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { ApiClient } from '../core/api/api-client';
import {
  BrokerAccount,
  BrokerAccountOverview,
  BrokerCapability,
  BrokerOrderRow,
  CalendarSummary,
  ConfirmOrderRequest,
  CreateOrderRequest,
  IBKRGatewayAuthStatus,
  IBKRGatewayDiscoverResult,
  IBKRGatewayProbeResult,
  IBKRRuntimeConfig,
  LiveDisclaimer,
  TradeStationAppCredentials,
  TradeStationDiscoverResult,
  TradeStationOAuthStartResult,
  TradeStationRuntimeConfig,
} from '../core/models/broker.model';

@Injectable({ providedIn: 'root' })
export class BrokerStore {
  private readonly api = inject(ApiClient);

  private readonly _registry = signal<BrokerCapability[]>([]);
  private readonly _accounts = signal<BrokerAccount[]>([]);
  private readonly _overview = signal<BrokerAccountOverview | null>(null);
  private readonly _orders = signal<BrokerOrderRow[]>([]);
  private readonly _calendar = signal<CalendarSummary | null>(null);
  private readonly _disclaimer = signal<LiveDisclaimer | null>(null);
  private readonly _busy = signal(false);
  private readonly _error = signal<string | null>(null);

  readonly registry = this._registry.asReadonly();
  readonly accounts = this._accounts.asReadonly();
  readonly overview = this._overview.asReadonly();
  readonly orders = this._orders.asReadonly();
  readonly calendar = this._calendar.asReadonly();
  readonly disclaimer = this._disclaimer.asReadonly();
  readonly busy = this._busy.asReadonly();
  readonly error = this._error.asReadonly();

  readonly pendingDrafts = computed(() =>
    this._orders().filter(
      (o) => o.status === 'draft' || o.status === 'confirmed',
    ),
  );

  setError(msg: string | null): void {
    this._error.set(msg);
  }

  loadRegistry(): Observable<{ brokers: BrokerCapability[] }> {
    return this.api.get<{ brokers: BrokerCapability[] }>('/brokers/').pipe(
      tap((r) => this._registry.set(r.brokers || [])),
    );
  }

  loadCalendar(): Observable<CalendarSummary> {
    return this.api
      .get<CalendarSummary>('/brokers/calendar/')
      .pipe(tap((r) => this._calendar.set(r)));
  }

  loadAccounts(): Observable<BrokerAccount[]> {
    return this.api.get<BrokerAccount[] | { results: BrokerAccount[] }>(
      '/broker-accounts/',
    ).pipe(
      tap((r) => {
        const list = Array.isArray(r) ? r : r.results ?? [];
        this._accounts.set(list);
      }),
    ) as unknown as Observable<BrokerAccount[]>;
  }

  loadOverview(accountId: number): Observable<BrokerAccountOverview> {
    return this.api
      .get<BrokerAccountOverview>(`/broker-accounts/${accountId}/overview/`)
      .pipe(tap((r) => this._overview.set(r)));
  }

  loadOrders(accountId?: number, status?: string): Observable<BrokerOrderRow[]> {
    const qs = new URLSearchParams();
    if (accountId) qs.set('account', String(accountId));
    if (status) qs.set('status', status);
    const path = `/broker/orders/${qs.toString() ? `?${qs}` : ''}`;
    return this.api
      .get<BrokerOrderRow[] | { results: BrokerOrderRow[] }>(path)
      .pipe(
        tap((r) => {
          const list = Array.isArray(r) ? r : (r as { results: BrokerOrderRow[] }).results ?? [];
          this._orders.set(list);
        }),
      ) as unknown as Observable<BrokerOrderRow[]>;
  }

  loadDisclaimer(): Observable<{ current: LiveDisclaimer | null }> {
    return this.api
      .get<{ current: LiveDisclaimer | null }>('/disclaimers/current/')
      .pipe(tap((r) => this._disclaimer.set(r.current)));
  }

  // ----- mutations -----

  createAccount(body: {
    broker: string;
    mode: 'paper' | 'live';
    label: string;
    config?: Record<string, unknown>;
  }): Observable<BrokerAccount> {
    this._busy.set(true);
    return this.api
      .post<BrokerAccount>('/broker-accounts/', body)
      .pipe(
        tap({
          next: (acc) => {
            this._accounts.update((rows) => [acc, ...rows]);
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  deleteAccount(accountId: number): Observable<void> {
    this._busy.set(true);
    return this.api
      .delete<void>(`/broker-accounts/${accountId}/`)
      .pipe(
        tap({
          next: () => {
            this._accounts.update((rows) => rows.filter((r) => r.id !== accountId));
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  disconnectAccount(accountId: number): Observable<BrokerAccount> {
    this._busy.set(true);
    return this.api
      .post<BrokerAccount>(`/broker-accounts/${accountId}/disconnect/`, {})
      .pipe(
        tap({
          next: (acc) => {
            this._accounts.update((rows) =>
              rows.map((r) => (r.id === acc.id ? acc : r)),
            );
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  syncAccount(accountId: number): Observable<{ drift_detected: boolean; notes: string }> {
    return this.api.post<{ drift_detected: boolean; notes: string }>(
      `/broker-accounts/${accountId}/sync/`,
      {},
    );
  }

  acknowledgeDrift(accountId: number): Observable<{ ok: boolean }> {
    return this.api.post<{ ok: boolean }>(
      `/broker-accounts/${accountId}/acknowledge-drift/`,
      {},
    );
  }

  createDraftOrder(body: CreateOrderRequest): Observable<BrokerOrderRow> {
    this._busy.set(true);
    return this.api
      .post<BrokerOrderRow>('/broker/orders/', body)
      .pipe(
        tap({
          next: (row) => {
            this._orders.update((rows) => [row, ...rows]);
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  confirmOrder(orderId: number, body: ConfirmOrderRequest): Observable<BrokerOrderRow> {
    this._busy.set(true);
    return this.api
      .post<BrokerOrderRow>(`/broker/orders/${orderId}/confirm/`, body)
      .pipe(
        tap({
          next: (row) => {
            this._orders.update((rows) =>
              rows.map((r) => (r.id === row.id ? row : r)),
            );
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  cancelOrder(orderId: number): Observable<BrokerOrderRow> {
    this._busy.set(true);
    return this.api
      .post<BrokerOrderRow>(`/broker/orders/${orderId}/cancel/`, {})
      .pipe(
        tap({
          next: (row) => {
            this._orders.update((rows) =>
              rows.map((r) => (r.id === row.id ? row : r)),
            );
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  acceptDisclaimer(version: string): Observable<{ version: string; accepted_at: string }> {
    return this.api
      .post<{ version: string; accepted_at: string }>('/disclaimers/accept/', {
        version,
      })
      .pipe(
        tap(() => {
          const d = this._disclaimer();
          if (d && d.version === version) {
            this._disclaimer.set({ ...d, accepted: true });
          }
        }),
      );
  }

  // --- P3a-2: IBKR gateway connect actions -------------------------------

  getIBKRRuntimeConfig(): Observable<IBKRRuntimeConfig> {
    return this.api.get<IBKRRuntimeConfig>('/broker-accounts/ibkr/runtime-config/');
  }

  probeIBKRGateway(accountId: number): Observable<IBKRGatewayProbeResult> {
    return this.api.post<IBKRGatewayProbeResult>(
      `/broker-accounts/${accountId}/gateway/probe/`,
      {},
    );
  }

  getIBKRGatewayAuthStatus(accountId: number): Observable<IBKRGatewayAuthStatus> {
    return this.api.post<IBKRGatewayAuthStatus>(
      `/broker-accounts/${accountId}/gateway/auth-status/`,
      {},
    );
  }

  discoverIBKRAccounts(accountId: number): Observable<IBKRGatewayDiscoverResult> {
    return this.api.post<IBKRGatewayDiscoverResult>(
      `/broker-accounts/${accountId}/gateway/discover-accounts/`,
      {},
    );
  }

  activateIBKRAccount(
    accountId: number,
    ibkrAccountId: string,
  ): Observable<BrokerAccount> {
    this._busy.set(true);
    return this.api
      .post<BrokerAccount>(
        `/broker-accounts/${accountId}/gateway/activate/`,
        { account_id: ibkrAccountId },
      )
      .pipe(
        tap({
          next: (acc) => {
            this._accounts.update((rows) =>
              rows.map((r) => (r.id === acc.id ? acc : r)),
            );
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  // --- P3a-3: TradeStation OAuth + connect actions ------------------------

  getTradeStationRuntimeConfig(): Observable<TradeStationRuntimeConfig> {
    return this.api.get<TradeStationRuntimeConfig>(
      '/broker-accounts/tradestation/runtime-config/',
    );
  }

  getTradeStationAppCredentials(): Observable<TradeStationAppCredentials> {
    return this.api.get<TradeStationAppCredentials>(
      '/broker-accounts/tradestation/app-credentials/',
    );
  }

  saveTradeStationAppCredentials(
    body: { client_id: string; client_secret: string },
  ): Observable<TradeStationAppCredentials> {
    return this.api.put<TradeStationAppCredentials>(
      '/broker-accounts/tradestation/app-credentials/',
      body,
    );
  }

  startTradeStationOAuth(accountId: number): Observable<TradeStationOAuthStartResult> {
    return this.api.post<TradeStationOAuthStartResult>(
      `/broker-accounts/${accountId}/oauth/start/`,
      {},
    );
  }

  discoverTradeStationAccounts(accountId: number): Observable<TradeStationDiscoverResult> {
    return this.api.post<TradeStationDiscoverResult>(
      `/broker-accounts/${accountId}/tradestation/discover-accounts/`,
      {},
    );
  }

  /**
   * P3a-4: submit the Alpaca paper API key id + secret for a draft
   * account. The backend validates the credentials against
   * `GET /v2/account` on the paper host, encrypts + stores them, and
   * flips `connection_status` to `active`.
   */
  submitAlpacaCredentials(
    accountId: number,
    body: { api_key: string; api_secret: string },
  ): Observable<BrokerAccount> {
    this._busy.set(true);
    return this.api
      .post<BrokerAccount>(
        `/broker-accounts/${accountId}/credentials/`,
        body,
      )
      .pipe(
        tap({
          next: (acc) => {
            this._accounts.update((rows) =>
              rows.map((r) => (r.id === acc.id ? acc : r)),
            );
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }

  activateTradeStationAccount(
    accountId: number,
    tsAccountId: string,
  ): Observable<BrokerAccount> {
    this._busy.set(true);
    return this.api
      .post<BrokerAccount>(
        `/broker-accounts/${accountId}/tradestation/activate/`,
        { account_id: tsAccountId },
      )
      .pipe(
        tap({
          next: (acc) => {
            this._accounts.update((rows) =>
              rows.map((r) => (r.id === acc.id ? acc : r)),
            );
            this._busy.set(false);
          },
          error: () => this._busy.set(false),
        }),
      );
  }
}
