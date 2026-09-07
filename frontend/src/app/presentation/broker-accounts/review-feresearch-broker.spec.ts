/**
 * Review (feresearch) — broker-accounts proof tests.
 *
 *   `it.fails('F: …')`  = confirmed defect; the assertion states the CORRECT
 *                         behaviour and currently fails.
 *   `it('evidence: …')` = passes today and documents the behaviour.
 */
import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { config as rxConfig, of, throwError } from 'rxjs';

import { BrokerAccountOverviewPage } from './account-overview.page';
import { PendingOrdersPage } from './pending-orders.page';
import { BrokerStore } from '../../abstraction/broker.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { BrokerOrderRow } from '../../core/models/broker.model';
import { routes } from '../../app.routes';

function row(overrides: Partial<BrokerOrderRow> = {}): BrokerOrderRow {
  return {
    id: 1, broker_account: 1, client_order_id: 'c', decision_id: null,
    ticker: 'AAPL', side: 'buy', quantity: '10', order_type: 'market',
    limit_price: null, stop_price: null, trail_price: null, trail_percent: null,
    time_in_force: 'day', status: 'submitted', idempotency_state: 'unsubmitted',
    broker_order_id: '', confirmed_at: null, confirmation_method: '',
    queued_until_open: false, is_held: false, release_eta: null, release_after: null,
    submitted_at: null, filled_at: null,
    cancelled_at: null, avg_fill_price: null, filled_quantity: '0',
    error_message: '', group_id: null, parent_order: null, leg_role: '',
    legs: [], group_status: null, notional_estimate: '1000.00',
    created_at: '2026-01-01T00:00:00Z', ...overrides,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

function overviewPage(orders: BrokerOrderRow[], cancelResult: 'ok' | 'reject'): {
  page: Cmp; calls: string[];
} {
  const calls: string[] = [];
  const store = {
    orders: signal(orders).asReadonly(),
    overview: signal({ account: { id: 1, broker: 'alpaca_paper' } }).asReadonly(),
    registry: signal([]).asReadonly(),
    loadOverview: () => of(null),
    loadOrders: () => of(orders),
    loadRegistry: () => of({ brokers: [] }),
    cancelOrder: (id: number) => {
      calls.push(`cancel:${id}`);
      return cancelResult === 'ok'
        ? of(row({ id, status: 'cancelled' }))
        : throwError(() => ({ status: 409, error: { detail: 'order is not cancellable' } }));
    },
  } as unknown as BrokerStore;
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      { provide: BrokerStore, useValue: store },
      { provide: TickerProfileStore, useValue: { fetchProfile: () => of(null) } },
      { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => '1' } } } },
      { provide: Router, useValue: { navigate: () => Promise.resolve(true) } },
    ],
  });
  const page = TestBed.runInInjectionContext(() => new BrokerAccountOverviewPage());
  return { page, calls };
}

function pendingPage(orders: BrokerOrderRow[]): Cmp {
  const store = {
    orders: signal(orders).asReadonly(),
    accounts: signal([]).asReadonly(),
    loadAccounts: () => of([]),
    loadOrders: () => of(orders),
  } as unknown as BrokerStore;
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [{ provide: BrokerStore, useValue: store }] });
  return TestBed.runInInjectionContext(() => new PendingOrdersPage());
}

describe('F — a `pending_open` order (held locally until the next open) is shown on NO broker surface', () => {
  const held = row({
    id: 42,
    status: 'pending_open' as BrokerOrderRow['status'],
    is_held: true,
    release_eta: '2026-09-08T13:30:00Z',
    release_after: '2026-09-08T13:30:00Z',
  });

  it('evidence: account overview "Working orders" filters submitted|partial only', () => {
    const { page } = overviewPage([held, row({ id: 1, status: 'submitted' })], 'ok');
    // "Working" still means live-at-the-broker …
    expect(page.working().map((o: BrokerOrderRow) => o.id)).toEqual([1]);
    // … and the held order now has its own section instead of vanishing.
    expect(page.heldOrders().map((o: BrokerOrderRow) => o.id)).toEqual([42]);
    expect(page.releaseLabel(held)).toContain('Sep');
  });

  it('evidence: the Pending orders page filters draft|confirmed only', () => {
    const page = pendingPage([held, row({ id: 2, status: 'draft' })]);
    expect(page.pending().map((o: BrokerOrderRow) => o.id)).toEqual([2]);
    expect(page.held().map((o: BrokerOrderRow) => o.id)).toEqual([42]);
  });

  it('F: an order that will auto-submit at the open must be visible somewhere', () => {
    const { page } = overviewPage([held], 'ok');
    const pending = pendingPage([held]);
    expect(page.working().length + page.heldOrders().length + pending.held().length)
      .toBeGreaterThan(0);
  });

  it('a held order can be cancelled from the account page', () => {
    const { page, calls } = overviewPage([held], 'ok');
    page.onCancel(held);
    expect(calls).toEqual(['cancel:42']);
  });
});

describe('F — cancelling a working order swallows the backend refusal', () => {
  // `onCancel()` subscribes with `{ next }` only, so an HTTP error is handed to
  // RxJS's unhandled-error path (a console error in the browser) and the page
  // shows nothing. Capture that path here so the proof is explicit.
  // RxJS reports the unhandled error on a macrotask, so flush one before asserting.
  async function withUnhandled<T>(fn: (captured: unknown[]) => T | Promise<T>): Promise<T> {
    const captured: unknown[] = [];
    const prev = rxConfig.onUnhandledError;
    rxConfig.onUnhandledError = (e) => captured.push(e);
    try {
      const out = await fn(captured);
      await new Promise((r) => setTimeout(r, 0));
      return out;
    } finally {
      await new Promise((r) => setTimeout(r, 0));
      rxConfig.onUnhandledError = prev;
    }
  }

  it('evidence: a 409 "order is not cancellable" leaves lastError null and goes to the unhandled-error sink', async () => {
    const captured = await withUnhandled(async (captured) => {
      const { page, calls } = overviewPage([row({ id: 7, status: 'submitted' })], 'reject');
      page.onCancel(row({ id: 7, status: 'submitted' }));
      expect(calls).toEqual(['cancel:7']);
      // FIXED: the refusal is handled by the page, not by RxJS's sink.
      expect(page.lastError()).toContain('not cancellable');
      return captured;
    });
    expect(captured).toHaveLength(0);
  });

  it('F: a refused cancel must surface an error to the user', async () => {
    await withUnhandled(async () => {
      const { page } = overviewPage([row({ id: 7, status: 'submitted' })], 'reject');
      page.onCancel(row({ id: 7, status: 'submitted' }));
      await new Promise((r) => setTimeout(r, 0));
      expect(page.lastError()).toContain('not cancellable');
    });
  });
});

describe('evidence — /broker-accounts/pending is a routed page with no in-app entry point', () => {
  it('the route exists (deep-link only; no nav item, list-page link or order-modal link references it — see grep in the review)', () => {
    expect(routes.some((r) => r.path === 'broker-accounts/pending')).toBe(true);
  });
});
