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
    aggregate_nav: '300000',
    peak_equity: '305000',
    drawdown_pct: 1.6,
    fund_dd_halt_pct: '6',
    per_account: [
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
        validation_passed: true,
        can_enable: false,
        setup_hint: null,
        has_backtest: true,
      },
    ],
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

  it('hintFor falls back to the validation-gate message when setup_hint is empty', () => {
    const cmp = setup(overview());
    expect(cmp.hintFor({ setup_hint: null })).toContain('validation backtest');
    expect(cmp.hintFor({ setup_hint: '   ' })).toContain('validation backtest');
    expect(cmp.hintFor({ setup_hint: 'Validated — open Autopilot and enable.' })).toBe(
      'Validated — open Autopilot and enable.',
    );
  });
});
