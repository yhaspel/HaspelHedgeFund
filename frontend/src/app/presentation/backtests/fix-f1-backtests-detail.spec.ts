import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { BehaviorSubject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { BacktestsStore } from '../../abstraction/backtests.store';
import { engineVersionBadge, engineVersionLabel } from '../../core/models/backtest.model';
import { ConfirmService } from '../shared/confirm.service';
import { BacktestsDetailPage } from './backtests-detail.page';

/**
 * WP F1 — BacktestsDetailPage:
 *  • the equity chart's screen-reader label read `bt.total_return_pct` /
 *    `bt.oos_sharpe`, LIST-serializer fields the detail payload leaves null, so
 *    every backtest announced "OOS return 0%, OOS Sharpe 0";
 *  • it read `route.snapshot` once, so /backtests/1 → /backtests/2 kept the old
 *    backtest on screen under the new url;
 *  • `engine_version` was nowhere, although v1 and v2 economics are not
 *    comparable.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

const DETAIL = {
  id: 1,
  name: 'WF 2023-2025',
  status: 'done',
  baseline: 'universe_ew',
  // The LIST fields the old label read — null on the detail payload.
  total_return_pct: null,
  oos_sharpe: null,
  stitched_sharpe: null,
  engine_version: 2,
  metrics: { total_return_pct: 41.25, sharpe: 1.34 },
};

function build(params: BehaviorSubject<ReturnType<typeof convertToParamMap>>, current: unknown) {
  TestBed.resetTestingModule();
  const poll = vi.fn();
  TestBed.configureTestingModule({
    providers: [
      {
        provide: BacktestsStore,
        useValue: {
          current: signal(current).asReadonly(),
          equity: signal([]).asReadonly(),
          equityBenchmarks: signal([]).asReadonly(),
          rollingSharpe: signal([]).asReadonly(),
          deflation: signal(null).asReadonly(),
          pollError: signal(null).asReadonly(),
          isPolling: signal(false).asReadonly(),
          poll,
          stopPolling: vi.fn(),
        },
      },
      { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true), notify: () => Promise.resolve() } },
      { provide: Router, useValue: { navigate: vi.fn() } },
      {
        provide: ActivatedRoute,
        useValue: { paramMap: params.asObservable(), snapshot: { paramMap: params.value } },
      },
    ],
  });
  const page: Cmp = TestBed.runInInjectionContext(() => new BacktestsDetailPage());
  return { page, poll };
}

describe('fix-f1 · BacktestsDetailPage', () => {
  it('describes the equity chart from the metrics block, not the null list fields', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const { page } = build(params, DETAIL);
    const label: string = page.equityChartLabel();
    expect(label).toContain('41.25%');
    expect(label).toContain('1.34');
    expect(label).not.toContain('OOS return 0%');
    expect(label).toContain('Engine v2');
    page.ngOnDestroy();
  });

  it('says results are unavailable rather than announcing zeros for a running backtest', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const { page } = build(params, { ...DETAIL, status: 'running', metrics: null });
    expect(page.equityChartLabel()).toContain('not available yet');
    page.ngOnDestroy();
  });

  it('reloads when the :id changes in place', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const { page, poll } = build(params, DETAIL);
    page.ngOnInit();
    expect(poll).toHaveBeenCalledWith(1, 3000);

    params.next(convertToParamMap({ id: '2' }));
    expect(poll).toHaveBeenCalledWith(2, 3000);
    expect(poll).toHaveBeenCalledTimes(2);

    // A re-emit of the same id is a no-op.
    params.next(convertToParamMap({ id: '2' }));
    expect(poll).toHaveBeenCalledTimes(2);
    page.ngOnDestroy();
  });

  it('stops following the route and stops polling on destroy', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const { page, poll } = build(params, DETAIL);
    page.ngOnInit();
    page.ngOnDestroy();
    params.next(convertToParamMap({ id: '9' }));
    expect(poll).toHaveBeenCalledTimes(1);
  });
});

describe('fix-f1 · engine_version labels', () => {
  it('names v2 as the current economics and v1 as not comparable', () => {
    expect(engineVersionLabel(2)).toContain('carried book');
    expect(engineVersionLabel(1)).toContain('not comparable');
    expect(engineVersionLabel(null)).toContain('not recorded');
    expect(engineVersionBadge(2)).toBe('engine v2');
    expect(engineVersionBadge(undefined)).toBe('engine v?');
  });
});
