import { describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { OrderConfirmModalComponent } from './order-confirm.modal';
import { BrokerStore } from '../../abstraction/broker.store';
import {
  BrokerOrderRow,
  ConfirmOrderRequest,
} from '../../core/models/broker.model';

function makeOrder(overrides: Partial<BrokerOrderRow> = {}): BrokerOrderRow {
  return {
    id: 1,
    broker_account: 1,
    client_order_id: 'coid-1',
    decision_id: null,
    ticker: 'AAPL',
    side: 'buy',
    quantity: '15',
    order_type: 'market',
    limit_price: null,
    stop_price: null,
    time_in_force: 'day',
    status: 'draft',
    idempotency_state: 'unsubmitted',
    broker_order_id: '',
    confirmed_at: null,
    confirmation_method: '',
    queued_until_open: false,
    submitted_at: null,
    filled_at: null,
    cancelled_at: null,
    avg_fill_price: null,
    filled_quantity: '0',
    error_message: '',
    group_id: null,
    parent_order: null,
    leg_role: '',
    legs: [],
    group_status: null,
    trail_price: null,
    trail_percent: null,
    notional_estimate: '0.00',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

/** A bracket entry anchor (limit entry) carrying its stop-loss + take-profit
 *  legs, as the serializer returns it. */
function makeBracketEntry(overrides: Partial<BrokerOrderRow> = {}): BrokerOrderRow {
  return makeOrder({
    order_type: 'limit',
    limit_price: '100',
    quantity: '10',
    leg_role: 'entry',
    group_id: 'g-1',
    group_status: 'draft',
    legs: [
      makeOrder({
        id: 2, leg_role: 'stop_loss', order_type: 'stop',
        side: 'sell', stop_price: '95', parent_order: 1,
      }),
      makeOrder({
        id: 3, leg_role: 'take_profit', order_type: 'limit',
        side: 'sell', limit_price: '110', parent_order: 1,
      }),
    ],
    ...overrides,
  });
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

/** Build the component inside an injection context so `inject(BrokerStore)`
 *  resolves — no fixture/change-detection needed to exercise the gate logic. */
function setup(confirmOrder: BrokerStore['confirmOrder']): Cmp {
  TestBed.configureTestingModule({
    providers: [
      { provide: BrokerStore, useValue: { confirmOrder } as Partial<BrokerStore> },
    ],
  });
  return TestBed.runInInjectionContext(() => new OrderConfirmModalComponent());
}

describe('OrderConfirmModalComponent typed-ticker confirmation gate', () => {
  it('does not require the typed ticker when the client estimate is under the threshold', () => {
    const cmp = setup(() => of(makeOrder()));
    cmp.order = makeOrder({ notional_estimate: '0.00' });

    expect(cmp.typedNeeded()).toBe(false);
    expect(cmp.canConfirm()).toBe(true); // a small/zero-estimate order submits freely
  });

  it('requires + shows the typed ticker up-front when the client estimate crosses the threshold', () => {
    const cmp = setup(() => of(makeOrder()));
    cmp.order = makeOrder({
      order_type: 'limit',
      limit_price: '200',
      notional_estimate: '3000.00',
    });

    expect(cmp.typedNeeded()).toBe(true);
    expect(cmp.canConfirm()).toBe(false); // blocked until the ticker is typed

    cmp.typedTicker = 'aapl'; // case-insensitive match
    expect(cmp.canConfirm()).toBe(true);
  });

  it('reveals the typed-ticker input when the backend gate rejects a market order it repriced over the threshold', () => {
    let sentBody: ConfirmOrderRequest | undefined;
    const confirmOrder = vi.fn((_id: number, body: ConfirmOrderRequest) => {
      sentBody = body;
      return throwError(() => ({
        error: {
          detail: 'orders over $1000 require typing the ticker (AAPL) to confirm',
          code: 'typed_confirmation_required',
        },
      }));
    });
    const cmp = setup(confirmOrder as unknown as BrokerStore['confirmOrder']);
    cmp.order = makeOrder({ notional_estimate: '0.00' }); // 15 shares, no price → estimate 0

    // The client cannot predict the gate, so the input starts hidden and the
    // first submit goes through without a typed confirmation.
    expect(cmp.typedNeeded()).toBe(false);
    cmp.onConfirm();

    expect(confirmOrder).toHaveBeenCalledTimes(1);
    expect(sentBody?.typed_confirmation).toBeUndefined();

    // After the 422 the input is revealed so the user can satisfy the gate.
    expect(cmp.typedNeeded()).toBe(true);
    expect(cmp.error()).toContain('typing the ticker');
    expect(cmp.canConfirm()).toBe(false); // still blocked until typed
  });

  it('sends the typed ticker on the retry after the input is revealed', () => {
    let sentBody: ConfirmOrderRequest | undefined;
    const confirmOrder = vi.fn((_id: number, body: ConfirmOrderRequest) => {
      sentBody = body;
      return of(makeOrder({ status: 'submitted' }));
    });
    const cmp = setup(confirmOrder as unknown as BrokerStore['confirmOrder']);
    cmp.order = makeOrder({ notional_estimate: '0.00' });

    let emitted: BrokerOrderRow | null = null;
    cmp.confirmed.subscribe((row: BrokerOrderRow) => (emitted = row));

    // Simulate the post-rejection state: input revealed, user typed the ticker.
    cmp.typedRevealed.set(true);
    cmp.typedTicker = 'AAPL';

    expect(cmp.canConfirm()).toBe(true);
    cmp.onConfirm();

    expect(confirmOrder).toHaveBeenCalledTimes(1);
    expect(sentBody?.typed_confirmation).toBe('AAPL');
    expect(emitted).not.toBeNull();
  });
});

describe('OrderConfirmModalComponent bracket max-loss / target-gain', () => {
  it('computes max loss and target gain for a long limit-entry bracket', () => {
    const cmp = setup(() => of(makeOrder()));
    cmp.order = makeBracketEntry();
    expect(cmp.isGroupEntry()).toBe(true);
    // Long 10 @100: stop 95 → max loss 50; take-profit 110 → gain 100.
    expect(cmp.maxLoss()).toBe(50);
    expect(cmp.targetGain()).toBe(100);
  });

  it('flips the signs for a short bracket', () => {
    const cmp = setup(() => of(makeOrder()));
    cmp.order = makeBracketEntry({
      side: 'sell',
      legs: [
        makeOrder({ id: 2, leg_role: 'stop_loss', order_type: 'stop',
          side: 'buy', stop_price: '105', parent_order: 1 }),
        makeOrder({ id: 3, leg_role: 'take_profit', order_type: 'limit',
          side: 'buy', limit_price: '90', parent_order: 1 }),
      ],
    });
    expect(cmp.maxLoss()).toBe(50);     // (105-100)*10
    expect(cmp.targetGain()).toBe(100); // (100-90)*10
  });

  it('omits max loss for a market entry (estimated server-side on submit)', () => {
    const cmp = setup(() => of(makeOrder()));
    cmp.order = makeBracketEntry({ order_type: 'market', limit_price: null });
    expect(cmp.isMarketEntry()).toBe(true);
    expect(cmp.maxLoss()).toBeNull();
    expect(cmp.targetGain()).toBeNull();
  });

  it('flags a stop-limit protective leg', () => {
    const cmp = setup(() => of(makeOrder()));
    cmp.order = makeBracketEntry({
      legs: [
        makeOrder({ id: 2, leg_role: 'stop_loss', order_type: 'stop_limit',
          side: 'sell', stop_price: '95', limit_price: '94', parent_order: 1 }),
      ],
    });
    expect(cmp.hasStopLimitExit()).toBe(true);
  });
});
