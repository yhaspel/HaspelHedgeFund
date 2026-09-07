import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BacktestsStore } from '../../abstraction/backtests.store';
import { BacktestComparePanelComponent } from './backtest-compare-panel.component';
import { BacktestsComparePage } from './backtests-compare.page';

/**
 * WAVE 3 F2 item 9 — the compare page folded into the detail page.
 *
 * `/backtests/:id/compare` used to render outside the app shell (its own
 * `min-h-screen` background, no nav, no breadcrumb), so comparing meant
 * leaving the backtest you were reading. The UI is now a panel on the detail
 * page; the route survives as a redirect so bookmarks do not 404, and both of
 * the earlier bug fixes (per-metric `higherIsBetter` colouring, missing
 * metrics as "—") travel with it.
 */

const LIST = [
  { id: 1, name: 'A', status: 'done', engine_version: 2 },
  { id: 2, name: 'B', status: 'done', engine_version: 2 },
  { id: 3, name: 'Legacy engine', status: 'done', engine_version: 1 },
  { id: 4, name: 'Still running', status: 'running', engine_version: 2 },
];

function storeDouble(compare: unknown = null) {
  return {
    list: signal(LIST),
    listBacktests: () => of(LIST),
    compare: () => of(compare),
  } as unknown as BacktestsStore;
}

describe('wave3-f2 · /backtests/:id/compare redirects into the detail page', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  function mount(params: Record<string, string>, query: Record<string, string>) {
    TestBed.configureTestingModule({
      imports: [BacktestsComparePage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: convertToParamMap(params),
              queryParamMap: convertToParamMap(query),
            },
          },
        },
      ],
    });
    const router = TestBed.inject(Router);
    const nav = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(BacktestsComparePage);
    fixture.detectChanges();
    return nav;
  }

  it('opens the panel on the detail page, keeping the old link alive', () => {
    const nav = mount({ id: '7' }, {});
    expect(nav).toHaveBeenCalledWith(['/backtests', 7], {
      queryParams: { compare: '1' },
      replaceUrl: true,
    });
  });

  it('carries a named B side through as the pre-selected comparison', () => {
    const nav = mount({ id: '7' }, { b: '9' });
    expect(nav).toHaveBeenCalledWith(['/backtests', 7], {
      queryParams: { compare: '9' },
      replaceUrl: true,
    });
  });

  it('falls back to the list when the url names no usable backtest', () => {
    const nav = mount({ id: 'nonsense' }, {});
    expect(nav).toHaveBeenCalledWith(['/backtests'], { replaceUrl: true });
  });
});

describe('wave3-f2 · <hf-backtest-compare-panel>', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  function mount(store: BacktestsStore) {
    TestBed.configureTestingModule({
      imports: [BacktestComparePanelComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        { provide: BacktestsStore, useValue: store },
      ],
    });
    const fixture = TestBed.createComponent(BacktestComparePanelComponent);
    fixture.componentRef.setInput('backtestId', 1);
    fixture.detectChanges();
    return fixture;
  }

  it('offers only finished backtests, and never A itself', () => {
    const fixture = mount(storeDouble());
    expect(fixture.componentInstance.comparable().map((b) => b.id)).toEqual([2, 3]);
  });

  it('pre-selects B from `initialCompareId` and loads that pair', () => {
    const store = storeDouble({
      a: { id: 1, name: 'A', points: [], metrics: { total_return_pct: 10 } },
      b: { id: 2, name: 'B', points: [], metrics: { total_return_pct: 14 } },
    });
    const spy = vi.spyOn(store, 'compare');
    TestBed.configureTestingModule({
      imports: [BacktestComparePanelComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        { provide: BacktestsStore, useValue: store },
      ],
    });
    const fixture = TestBed.createComponent(BacktestComparePanelComponent);
    fixture.componentRef.setInput('backtestId', 1);
    fixture.componentRef.setInput('initialCompareId', 2);
    fixture.detectChanges();
    expect(spy).toHaveBeenCalledWith(1, 2);
    expect(fixture.nativeElement.querySelector('[data-test="compare-metrics"]')).not.toBeNull();
  });

  it('keeps the engine-version mismatch banner', () => {
    const store = storeDouble({
      a: { id: 1, name: 'A', points: [], metrics: {} },
      b: { id: 3, name: 'Legacy engine', points: [], metrics: {} },
    });
    const fixture = mount(store);
    fixture.componentInstance.idB = 3;
    fixture.componentInstance.load();
    fixture.detectChanges();
    expect(fixture.componentInstance.engineMismatch()).toBe(true);
    expect(fixture.nativeElement.querySelector('[data-test="engine-mismatch"]')).not.toBeNull();
  });

  it('does not warn when both sides came from the same engine', () => {
    const store = storeDouble({
      a: { id: 1, name: 'A', points: [], metrics: {} },
      b: { id: 2, name: 'B', points: [], metrics: {} },
    });
    const fixture = mount(store);
    fixture.componentInstance.idB = 2;
    fixture.componentInstance.load();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-test="engine-mismatch"]')).toBeNull();
  });

  it('keeps the per-metric higherIsBetter colouring through the move', () => {
    const store = storeDouble({
      a: {
        id: 1,
        name: 'A',
        points: [],
        metrics: { total_return_pct: 10, max_drawdown_pct: 8, turnover_pct: 100 },
      },
      b: {
        id: 2,
        name: 'B',
        points: [],
        metrics: { total_return_pct: 14, max_drawdown_pct: 25, turnover_pct: 400 },
      },
    });
    const fixture = mount(store);
    fixture.componentInstance.idB = 2;
    fixture.componentInstance.load();
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    // A bigger drawdown / higher turnover is WORSE even though the delta is +.
    expect(
      (el.querySelector('[data-test="compare-delta-max_drawdown_pct"]') as HTMLElement).style.color,
    ).toBe('var(--acc-short-fg)');
    expect(
      (el.querySelector('[data-test="compare-delta-turnover_pct"]') as HTMLElement).style.color,
    ).toBe('var(--acc-short-fg)');
    // …and a better return is still green.
    expect(
      (el.querySelector('[data-test="compare-delta-total_return_pct"]') as HTMLElement).style.color,
    ).toBe('var(--acc-long-fg)');
  });

  it('renders a missing metric as "—" with no fabricated delta', () => {
    const store = storeDouble({
      a: { id: 1, name: 'A', points: [], metrics: { total_return_pct: 12.5 } },
      b: { id: 2, name: 'B (running)', points: [], metrics: null },
    });
    const fixture = mount(store);
    fixture.componentInstance.idB = 2;
    fixture.componentInstance.load();
    fixture.detectChanges();
    const cell = fixture.nativeElement.querySelector(
      '[data-test="compare-delta-total_return_pct"]',
    ) as HTMLElement;
    expect(cell.textContent!.trim()).toBe('—');
    expect(cell.style.color).toBe('');
  });

  it('emits `closed` so the detail page can put the panel away', () => {
    const fixture = mount(storeDouble());
    let closed = 0;
    fixture.componentInstance.closed.subscribe(() => (closed += 1));
    (
      fixture.nativeElement.querySelector('[data-test="compare-close"]') as HTMLButtonElement
    ).click();
    expect(closed).toBe(1);
  });
});
