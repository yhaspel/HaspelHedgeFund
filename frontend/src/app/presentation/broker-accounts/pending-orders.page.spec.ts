import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { of } from 'rxjs';

import { PendingOrdersPage } from './pending-orders.page';
import { BrokerStore } from '../../abstraction/broker.store';
import { BrokerOrderRow } from '../../core/models/broker.model';

function row(overrides: Partial<BrokerOrderRow> = {}): BrokerOrderRow {
  return {
    id: 1, broker_account: 1, client_order_id: 'c', decision_id: null,
    ticker: 'AAPL', side: 'buy', quantity: '10', order_type: 'limit',
    limit_price: '100', stop_price: null, trail_price: null, trail_percent: null,
    time_in_force: 'day', status: 'confirmed', idempotency_state: 'unsubmitted',
    broker_order_id: '', confirmed_at: null, confirmation_method: '',
    queued_until_open: false, submitted_at: null, filled_at: null,
    cancelled_at: null, avg_fill_price: null, filled_quantity: '0',
    error_message: '', group_id: null, parent_order: null, leg_role: '',
    legs: [], group_status: null, notional_estimate: '1000.00',
    created_at: '2026-01-01T00:00:00Z', ...overrides,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

function setup(orders: BrokerOrderRow[]): Cmp {
  const store = {
    orders: signal(orders).asReadonly(),
    accounts: signal([]).asReadonly(),
    loadAccounts: () => of([]),
    loadOrders: () => of([]),
  } as unknown as BrokerStore;
  TestBed.configureTestingModule({
    providers: [{ provide: BrokerStore, useValue: store }],
  });
  return TestBed.runInInjectionContext(() => new PendingOrdersPage());
}

describe('PendingOrdersPage grouped legs', () => {
  it('lists the anchor only — protective legs are nested, not standalone rows', () => {
    const entry = row({
      id: 1, leg_role: 'entry', group_id: 'g', group_status: 'draft',
      legs: [
        row({ id: 2, leg_role: 'stop_loss', order_type: 'stop', side: 'sell',
          stop_price: '95', limit_price: null, parent_order: 1 }),
        row({ id: 3, leg_role: 'take_profit', order_type: 'limit', side: 'sell',
          limit_price: '110', parent_order: 1 }),
      ],
    });
    const sl = row({ id: 2, parent_order: 1, leg_role: 'stop_loss' });
    const tp = row({ id: 3, parent_order: 1, leg_role: 'take_profit' });
    const cmp = setup([entry, sl, tp]);

    const pending = cmp.pending();
    expect(pending.map((o: BrokerOrderRow) => o.id)).toEqual([1]);
    expect(cmp.groupKind(entry)).toBe('bracket');
  });

  it('labels OCO and OTO groups', () => {
    const ocoAnchor = row({
      id: 1, leg_role: 'take_profit', group_id: 'g',
      legs: [row({ id: 2, leg_role: 'stop_loss', order_type: 'stop', parent_order: 1 })],
    });
    const otoAnchor = row({
      id: 3, leg_role: 'entry', group_id: 'h',
      legs: [row({ id: 4, leg_role: 'stop_loss', order_type: 'stop', parent_order: 3 })],
    });
    const cmp = setup([ocoAnchor, otoAnchor]);
    expect(cmp.groupKind(ocoAnchor)).toBe('OCO');
    expect(cmp.groupKind(otoAnchor)).toBe('OTO');
  });

  it('renders a readable leg label', () => {
    const cmp = setup([]);
    const sl = row({ leg_role: 'stop_loss', order_type: 'stop', stop_price: '95',
      limit_price: null });
    expect(cmp.legLabel(sl)).toContain('Stop-loss');
    expect(cmp.legLabel(sl)).toContain('$95.00');
  });
});
