import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it } from 'vitest';

import { BacktestsStore } from '../../abstraction/backtests.store';
import { BacktestComparePanelComponent } from './backtest-compare-panel.component';

/**
 * Review (fedesk) — the comparison table used to colour every "B − A" delta
 * green when positive. `max_drawdown_pct` and `turnover_pct` are positive
 * magnitudes (apps/backtests/metrics.py:39-44 "returned as a positive
 * fraction", ×100 at :321/:386), so a *worse* drawdown / *higher* turnover
 * was painted as an improvement.
 *
 * WAVE 3 (F2): the comparison UI moved out of the standalone
 * `/backtests/:id/compare` page and into `<hf-backtest-compare-panel>` on the
 * detail page (the route is now a redirect). The behaviour under test did not
 * change — only where it lives — so this spec follows it to the panel and its
 * assertions are unchanged.
 */
describe('review-fedesk · backtest compare delta colouring', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [BacktestComparePanelComponent],
      providers: [
        provideRouter([]),
        { provide: BacktestsStore, useValue: { list: signal([]), listBacktests: () => of([]), compare: () => of(null) } },
      ],
    });
  });

  it('paints a larger max drawdown (worse) and higher turnover in the long/green colour', () => {
    const fixture = TestBed.createComponent(BacktestComparePanelComponent);
    const page = fixture.componentInstance;
    page.idB = 2;
    page.data.set({
      a: { id: 1, name: 'A', points: [], metrics: { total_return_pct: 10, mean_oos_sharpe: 1, sharpe_deflation: 0.6, max_drawdown_pct: 8, turnover_pct: 100, baseline_return_pct: 5 } },
      b: { id: 2, name: 'B', points: [], metrics: { total_return_pct: 10, mean_oos_sharpe: 1, sharpe_deflation: 0.6, max_drawdown_pct: 25, turnover_pct: 400, baseline_return_pct: 5 } },
    });
    fixture.detectChanges();

    const rows = Array.from(fixture.nativeElement.querySelectorAll('tbody tr')) as HTMLTableRowElement[];
    const byLabel = new Map(rows.map((r) => [r.cells[0].textContent!.trim(), r]));

    // FIXED: max drawdown and turnover declare higherIsBetter=false, so a
    // bigger magnitude is painted as a regression.
    const dd = byLabel.get('Max drawdown ↓ better')!;
    expect(dd.cells[3].textContent!.trim()).toBe('+17.00%'); // B has 17pp MORE drawdown …
    expect(dd.cells[3].style.color).toBe('var(--acc-short-fg)'); // … shown as worse

    const to = byLabel.get('Turnover (annualized) ↓ better')!;
    expect(to.cells[3].textContent!.trim()).toBe('+300.00%');
    expect(to.cells[3].style.color).toBe('var(--acc-short-fg)');

    // …and a genuinely-better return is still green.
    const bt = byLabel.get('Baseline return')!;
    expect(bt.cells[3].style.color).toBe('');
  });

  it('renders 0.00 (not "—") for a backtest B that has no metrics yet (running / failed)', () => {
    const fixture = TestBed.createComponent(BacktestComparePanelComponent);
    const page = fixture.componentInstance;
    page.data.set({
      a: { id: 1, name: 'A', points: [], metrics: { total_return_pct: 12.5, max_drawdown_pct: 8 } },
      b: { id: 2, name: 'B (running)', points: [], metrics: null },
    });
    fixture.detectChanges();
    const rows = Array.from(fixture.nativeElement.querySelectorAll('tbody tr')) as HTMLTableRowElement[];
    const ret = rows.find((r) => r.cells[0].textContent!.includes('Stitched OOS return'))!;
    // FIXED: no fabricated zero and no fabricated delta.
    expect(ret.cells[2].textContent!.trim()).toBe('—');
    expect(ret.cells[3].textContent!.trim()).toBe('—');
    expect(ret.cells[3].style.color).toBe('');
  });

  it('offers only finished backtests in the B picker', () => {
    const fixture = TestBed.createComponent(BacktestComparePanelComponent);
    const page = fixture.componentInstance;
    page.backtestId = 1;
    const rows = [
      { id: 1, status: 'done' },
      { id: 2, status: 'done' },
      { id: 3, status: 'running' },
      { id: 4, status: 'failed' },
    ] as never[];
    (page.store.list as unknown as { set: (v: unknown) => void }).set(rows);
    expect(page.comparable().map((b) => b.id)).toEqual([2]);
  });
});
