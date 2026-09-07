import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BacktestsStore } from '../../abstraction/backtests.store';
import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import { EstimateResponse } from '../../core/models/backtest.model';
import { BacktestsNewPage } from './backtests-new.page';

/**
 * Review (fedesk) — BacktestsNewPage client-side validation vs the backend
 * contract, and the pre-flight estimate that never goes stale.
 *
 * The `min="…"` attributes on the number inputs are advisory only: the page
 * never checks form validity before calling store.create(), and the backend
 * serializer has no lower bound on step_days / starting_cash either (see
 * apps/backtests/serializers.py). step_days=0 makes generate_folds() loop
 * forever (apps/backtests/walkforward.py:53-65).
 */
const EST: EstimateResponse = {
  n_trading_days: 500, n_rebalance_days: 100, n_universe: 20, n_invocations: 2000,
  n_llm_calls: 2000, est_total_usd: 1.5, est_minutes_optimistic: 5, est_minutes_upper: 10,
  budget_cap_usd: 4, exceeds_budget: false, by_agent: [],
};

class FakeBacktestsStore {
  readonly defaultUniverse = signal<string[]>(['AAPL', 'MSFT']);
  create: any = vi.fn(() => of({ id: 99 }));
  estimate = vi.fn(() => of(EST));
  loadDefaultUniverse = vi.fn(() => of({ universe: ['AAPL', 'MSFT'] }));
  strategyDefaults = vi.fn(() => of({}));
}

describe('review-fedesk · BacktestsNewPage validation gaps', () => {
  let page: BacktestsNewPage;
  let store: FakeBacktestsStore;

  beforeEach(() => {
    store = new FakeBacktestsStore();
    TestBed.configureTestingModule({
      providers: [
        { provide: BacktestsStore, useValue: store },
        { provide: ModelsStore, useValue: { loadAll: () => of({}), models: signal([]), defaultsMap: signal({}), prefs: signal(null) } },
        { provide: GraphsStore, useValue: { loadGraphs: () => of([]), graphs: signal([]) } },
        { provide: Router, useValue: { navigate: vi.fn() } },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({}) } } },
      ],
    });
    page = TestBed.runInInjectionContext(() => new BacktestsNewPage());
    page.ngOnInit();
  });

  it('submits step_days=0 / starting_cash=0 / oos_window_days=0 / negative bps with no client guard', () => {
    page.stepDays = 0;
    page.startingCash = 0;
    page.oosWindow = 0;
    page.commissionBps = -5;

    // FIXED: nothing leaves the page — not even the estimate call.
    page.estimateCost();
    expect(store.estimate).not.toHaveBeenCalled();
    expect(page.estimate()).toBeNull();

    page.submit();
    expect(store.create).not.toHaveBeenCalled();

    // …and each field says what is wrong.
    expect(page.fieldError('stepDays')).toBeTruthy();
    expect(page.fieldError('startingCash')).toBeTruthy();
    expect(page.fieldError('oosWindow')).toBeTruthy();
    expect(page.fieldError('commissionBps')).toBeTruthy();
    expect(page.isValid()).toBe(false);
  });

  it('rejects a step smaller than the OOS window (overlapping folds)', () => {
    page.oosWindow = 63;
    page.stepDays = 21;
    page.estimateCost();
    expect(store.estimate).not.toHaveBeenCalled();
    expect(page.fieldError('stepDays')).toContain('overlaps folds');
  });

  it('submits a universe / rebalance / persona set that was never estimated (stale pre-flight estimate)', () => {
    page.universeStr = 'AAPL';
    page.rebalance = 'monthly';
    page.estimateCost();
    const estimated = (store.estimate.mock.calls[0] as unknown as [Record<string, unknown>])[0];
    expect(estimated['universe']).toEqual(['AAPL']);
    expect(estimated['rebalance_frequency']).toBe('monthly');
    expect(page.estimate()).not.toBeNull();

    // The form stays editable while the estimate card is shown — but any change
    // to an estimate-relevant input now invalidates it.
    page.universeStr = 'AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, XOM';
    page.rebalance = 'daily';
    page.togglePersona('munger');
    page.togglePersona('graham');
    page.overrides.set({ buffett: 'anthropic/claude-opus-4' });
    expect(page.estimate()).toBeNull(); // FIXED: the $1.50 quote no longer applies

    // …and submit refuses until it is re-taken.
    page.submit();
    expect(store.create).not.toHaveBeenCalled();
    expect(page.error()).toContain('Re-run the cost estimate');

    page.estimateCost();
    expect(store.estimate).toHaveBeenCalledTimes(2); // re-estimated for the new shape
    page.submit();
    const created = (store.create.mock.calls[0] as unknown as [Record<string, unknown>])[0];
    expect(created['universe']).toHaveLength(10);
    expect(created['rebalance_frequency']).toBe('daily');
    expect(created['personas']).toEqual(['buffett', 'munger', 'graham']);
    expect(created['model_overrides']).toEqual({ buffett: 'anthropic/claude-opus-4' });
  });

  it('does not dedupe or validate universe symbols client-side', () => {
    page.universeStr = 'aapl, AAPL, , $$$, aapl';
    page.estimateCost();
    // FIXED: "$$$" is not a symbol, so nothing is sent.
    expect(store.estimate).not.toHaveBeenCalled();
    expect(page.fieldError('universe')).toContain('$$$');

    // With only duplicates, the list is deduped rather than sent three times.
    page.universeStr = 'aapl, AAPL, , aapl';
    page.estimateCost();
    const estimated = (store.estimate.mock.calls[0] as unknown as [Record<string, unknown>])[0];
    expect(estimated['universe']).toEqual(['AAPL']);
  });

  it('accepts an end date before the start date and asks the backend anyway', () => {
    page.startDate = '2025-12-31';
    page.endDate = '2023-01-02';
    page.estimateCost();
    // FIXED: caught locally.
    expect(store.estimate).not.toHaveBeenCalled();
    expect(page.fieldError('endDate')).toContain('after the start date');
  });

  it('flattens a backend 400 instead of dumping JSON at the user', () => {
    store.create = vi.fn(() =>
      throwError(() => ({
        status: 400,
        error: { step_days: ['Ensure this value is greater than or equal to 1.'] },
      })),
    );
    page.estimateCost();
    page.submit();
    expect(page.error()).toBe('Step days: Ensure this value is greater than or equal to 1.');
    expect(page.error()).not.toContain('{');
  });
});
