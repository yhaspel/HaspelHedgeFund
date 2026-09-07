import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { of } from 'rxjs';

import { FundDashboardPage } from './fund-dashboard.page';
import { FundStore } from '../../abstraction/fund.store';
import { MacroStore } from '../../abstraction/macro.store';
import { FundOverview } from '../../core/models/autopilot.model';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

function overview(overrides: Partial<FundOverview> = {}): FundOverview {
  return {
    fund_id: 1,
    name: 'Autonomous Fund',
    state: 'active',
    is_live: true,
    is_configured: true,
    broker_account: {
      id: 7,
      label: 'POOL',
      broker: 'alpaca_paper',
      broker_display: 'Alpaca',
      mode: 'paper',
      connection_status: 'active',
      last_synced_at: null,
      cash: '200000',
      nav: '300000',
      positions_count: 4,
      portfolio_id: 70,
    },
    aggregate_nav: '300000',
    peak_equity: '305000',
    drawdown_pct: 1.6,
    fund_dd_halt_pct: '6',
    members: [
      {
        strategy_id: 1,
        name: 'Multi-Factor',
        kind: 'long_short',
        state: 'active',
        is_enabled: true,
        nav: '100000',
        peak_equity: '100000',
        rolling_sharpe: 0.8,
        next_run_at: '2026-06-05T20:30:00Z',
        cron_description: 'At 04:30 PM, only on Friday',
        timezone: 'America/New_York',
        validation_passed: true,
        can_enable: false,
        setup_hint: null,
        has_backtest: true,
        allocation_pct: '50.00',
        initial_capital: '100000.00',
        cash: '60000.00',
        positions_count: 3,
        pnl_pct: 0.0,
        sleeve_portfolio_id: 71,
      },
      {
        strategy_id: 2,
        name: 'Trend',
        kind: 'global_macro',
        state: 'active',
        is_enabled: false,
        nav: '200000',
        peak_equity: null,
        rolling_sharpe: null,
        next_run_at: null,
        cron_description: 'At 04:45 PM, only on Friday',
        timezone: 'America/New_York',
        validation_passed: false,
        can_enable: false,
        setup_hint: 'Run a validation backtest to unlock the enable toggle.',
        has_backtest: false,
        allocation_pct: '50.00',
        initial_capital: '100000.00',
        cash: '200000.00',
        positions_count: 0,
        pnl_pct: 100.0,
        sleeve_portfolio_id: 72,
      },
    ],
    members_count: 2,
    allocation_total_pct: '100.00',
    unallocated_nav: '0.00',
    attribution_gap: { available: true, positions: {}, cash: '0.00' },
    leaving: [],
    reset: { ready: false, reason: 'positions', positions: 4, inflight_orders: 0 },
    inflight_orders: 0,
    correlation: { available: false, reason: 'insufficient_data', min_sample: 8 },
    recommendations: [],
    ...overrides,
  };
}

function setup(o: FundOverview | null): Cmp {
  const store = {
    fund: signal(o).asReadonly(),
    loadFund: () => of(o),
    haltFund: () => of({}),
    resumeFund: () => of({}),
  } as unknown as FundStore;
  // The page now renders the macro-regime strip (restored to the landing
  // page); stub the store so construction doesn't pull ApiClient/HttpClient.
  const macro = {
    snapshot: signal(null).asReadonly(),
    loadSnapshot: () => of(null),
  } as unknown as MacroStore;
  // Several specs build more than one page → start each from a clean TestBed.
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      { provide: FundStore, useValue: store },
      { provide: MacroStore, useValue: macro },
    ],
  });
  return TestBed.runInInjectionContext(() => new FundDashboardPage());
}

describe('FundDashboardPage', () => {
  it('exposes the fund overview from the store', () => {
    const cmp = setup(overview());
    expect(cmp.fund().aggregate_nav).toBe('300000');
    expect(cmp.cols()).toEqual([]); // correlation suppressed → no columns
  });

  it('derives correlation columns when the matrix is available', () => {
    const cmp = setup(
      overview({
        correlation: { available: true, matrix: { A: { A: 1, B: 0.2 }, B: { A: 0.2, B: 1 } } },
      }),
    );
    expect(cmp.cols()).toEqual(['A', 'B']);
  });

  it('names the kill switch after the live member count (never a hard-coded 3)', () => {
    expect(setup(overview()).haltLabel()).toBe('Halt all 2 strategies');
    expect(setup(overview({ members_count: 1 })).haltLabel()).toBe('Halt all 1 strategy');
    expect(setup(overview({ members_count: 0, members: [] })).haltLabel()).toBe('Halt fund');
  });

  it('opens the manage drawer by itself until the fund is configured with members', () => {
    expect(setup(overview()).manageOpen()).toBe(false);
    expect(setup(overview({ is_configured: false, broker_account: null })).manageOpen()).toBe(true);
    expect(setup(overview({ members_count: 0, members: [] })).manageOpen()).toBe(true);
    const cmp = setup(overview());
    cmp.toggleManage();
    expect(cmp.manageOpen()).toBe(true);
  });

  it('explains why a reset is not possible yet', () => {
    expect(setup(overview()).resetReason()).toContain('4 positions');
    expect(
      setup(
        overview({
          reset: { ready: false, reason: 'no_account', positions: 0, inflight_orders: 0 },
        }),
      ).resetReason(),
    ).toContain('paper account');
    expect(
      setup(
        overview({ reset: { ready: true, reason: null, positions: 0, inflight_orders: 0 } }),
      ).resetReason(),
    ).toBe('');
  });

  it('deep-links the not-live banner into the fund panel of the first disabled member', () => {
    expect(setup(overview()).setupLink()).toEqual(['/fund/strategies', 2]);
  });

  it('hintFor falls back to the validation-gate message when setup_hint is empty', () => {
    const cmp = setup(overview());
    expect(cmp.hintFor({ setup_hint: null })).toContain('validation backtest');
    expect(cmp.hintFor({ setup_hint: '   ' })).toContain('validation backtest');
    expect(cmp.hintFor({ setup_hint: 'Validated — open Autopilot and enable.' })).toBe(
      'Validated — open Autopilot and enable.',
    );
  });
});
