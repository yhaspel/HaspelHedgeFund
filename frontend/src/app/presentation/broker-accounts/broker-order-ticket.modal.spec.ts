import { describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { of } from 'rxjs';

import { BrokerOrderTicketModalComponent } from './broker-order-ticket.modal';
import { BrokerStore } from '../../abstraction/broker.store';
import {
  BrokerAccount,
  BrokerCapability,
  CreateOrderRequest,
} from '../../core/models/broker.model';

const CAP = {
  code: 'alpaca_paper',
  display_name: 'Alpaca (paper only)',
  supports_fractional: true,
  supports_bracket: true,
  supported_order_types: ['market', 'limit', 'stop', 'stop_limit', 'trailing_stop'],
  supported_time_in_force: ['day', 'gtc'],
} as unknown as BrokerCapability;

const ACCOUNT = {
  id: 1, broker: 'alpaca_paper', default_quantity_mode: 'whole',
} as unknown as BrokerAccount;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type DraftFn = (b: CreateOrderRequest) => any;

function setup(
  createDraftOrder: DraftFn = () => of({}),
  decisionOverrides: Record<string, unknown> = {},
): Cmp {
  const store = {
    registry: signal([CAP]).asReadonly(),
    loadRegistry: () => of({ brokers: [CAP] }),
    createDraftOrder,
  } as unknown as BrokerStore;
  TestBed.configureTestingModule({
    providers: [{ provide: BrokerStore, useValue: store }],
  });
  const cmp = TestBed.runInInjectionContext(
    () => new BrokerOrderTicketModalComponent(),
  );
  cmp.decision = {
    id: 1, ticker: 'AAPL', side: 'buy', targetQuantity: '10', ...decisionOverrides,
  };
  cmp.accounts = [ACCOUNT];
  cmp.ngOnInit();
  return cmp;
}

describe('BrokerOrderTicketModalComponent advanced order types', () => {
  it('offers bracket + trailing only when the broker supports them', () => {
    const cmp = setup();
    expect(cmp.supportsBracket()).toBe(true);
    expect(cmp.supportsType('trailing_stop')).toBe(true);
    expect(cmp.supportsType('stop_limit')).toBe(true);
  });

  it('forces whole shares for an advanced (bracket) order', () => {
    const cmp = setup();
    cmp.quantityMode = 'fractional';
    cmp.kind = 'bracket';
    cmp.onKindChange();
    expect(cmp.isAdvanced()).toBe(true);
    expect(cmp.quantityMode).toBe('whole');
  });

  it('builds a bracket request with both protective exits', () => {
    const createDraftOrder = vi.fn((_b: CreateOrderRequest) => of({}));
    const cmp = setup(createDraftOrder);
    cmp.kind = 'bracket';
    cmp.entryType = 'limit';
    cmp.limitPrice = 100;
    cmp.takeProfit = 110;
    cmp.stopLossStop = 95;
    cmp.quantity = 10;
    expect(cmp.canReview()).toBe(true);
    cmp.onReview();
    const body = createDraftOrder.mock.calls[0][0] as CreateOrderRequest;
    expect(body.order_class).toBe('bracket');
    expect(body.order_type).toBe('limit');
    expect(body.limit_price).toBe('100');
    expect(body.take_profit_limit_price).toBe('110');
    expect(body.stop_loss_stop_price).toBe('95');
    expect(body.stop_loss_limit_price).toBeNull();
  });

  it('builds an OTO request with exactly one exit', () => {
    const createDraftOrder = vi.fn((_b: CreateOrderRequest) => of({}));
    const cmp = setup(createDraftOrder);
    cmp.kind = 'oto';
    cmp.entryType = 'market';
    cmp.otoExit = 'stop_loss';
    cmp.stopLossStop = 95;
    cmp.quantity = 10;
    expect(cmp.showTakeProfit()).toBe(false);
    expect(cmp.showStopLoss()).toBe(true);
    expect(cmp.canReview()).toBe(true);
    cmp.onReview();
    const body = createDraftOrder.mock.calls[0][0] as CreateOrderRequest;
    expect(body.order_class).toBe('oto');
    expect(body.stop_loss_stop_price).toBe('95');
    expect(body.take_profit_limit_price).toBeNull();
  });

  it('builds a trailing-stop request by percent', () => {
    const createDraftOrder = vi.fn((_b: CreateOrderRequest) => of({}));
    const cmp = setup(createDraftOrder);
    cmp.kind = 'trailing_stop';
    cmp.trailMode = 'percent';
    cmp.trailValue = 5;
    cmp.quantity = 10;
    expect(cmp.canReview()).toBe(true);
    cmp.onReview();
    const body = createDraftOrder.mock.calls[0][0] as CreateOrderRequest;
    expect(body.order_type).toBe('trailing_stop');
    expect(body.trail_percent).toBe('5');
    expect(body.trail_price).toBeNull();
  });

  it('requires the stop trigger for a standalone stop', () => {
    const cmp = setup();
    cmp.kind = 'stop';
    cmp.quantity = 10;
    expect(cmp.canReview()).toBe(false); // no stop price yet
    cmp.stopPrice = 95;
    expect(cmp.canReview()).toBe(true);
  });
});

describe('BrokerOrderTicketModalComponent pre-fill from the run', () => {
  it('opens pre-set to a Bracket with the run levels when stop + target are present', () => {
    const cmp = setup(() => of({}), {
      stopLossPrice: 247.17, takeProfitPrice: 565.83,
    });
    expect(cmp.kind).toBe('bracket');
    expect(cmp.entryType).toBe('market');
    expect(cmp.stopLossStop).toBe(247.17);
    expect(cmp.takeProfit).toBe(565.83);
    expect(cmp.quantityMode).toBe('whole'); // advanced forces whole
  });

  it('opens as an OTO (stop only) when the run has no target', () => {
    const cmp = setup(() => of({}), { stopLossPrice: 247.17 });
    expect(cmp.kind).toBe('oto');
    expect(cmp.otoExit).toBe('stop_loss');
    expect(cmp.stopLossStop).toBe(247.17);
  });

  it('stays a plain market order when the decision carries no protective levels', () => {
    const cmp = setup();
    expect(cmp.kind).toBe('market');
  });
});
