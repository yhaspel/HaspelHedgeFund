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
  LiveDisclaimer,
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
}
