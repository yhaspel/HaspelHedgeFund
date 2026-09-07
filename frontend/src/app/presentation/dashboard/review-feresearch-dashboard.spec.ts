/**
 * Review (feresearch) — manual dashboard proof tests.
 *
 *   `it.fails('F: …')`  = confirmed defect; the assertion states the CORRECT
 *                         behaviour and currently fails.
 *   `it('evidence: …')` = passes today and documents the behaviour.
 */
import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { CUSTOM_ELEMENTS_SCHEMA, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { DashboardPage } from './dashboard.page';
import { AuthStore } from '../../abstraction/auth.store';
import { FundStore } from '../../abstraction/fund.store';
import { MacroStore } from '../../abstraction/macro.store';
import { PortfolioStore } from '../../abstraction/portfolio.store';
import { RunsStore } from '../../abstraction/runs.store';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';

function setup(opts: { portfolioFails: boolean }) {
  const portfolio = {
    overview: signal(null).asReadonly(),
    loadOverview: () =>
      opts.portfolioFails
        ? throwError(() => ({ status: 500, error: { detail: 'boom' } }))
        : of(null),
  };
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: AuthStore, useValue: {} },
      { provide: RunsStore, useValue: { runs: signal([]).asReadonly(), listRuns: () => of([]) } },
      { provide: MacroStore, useValue: { snapshot: signal(null).asReadonly(), loadSnapshot: () => of(null) } },
      { provide: StrategiesStore, useValue: { strategies: signal([]).asReadonly(), list: () => of([]) } },
      { provide: PortfolioStore, useValue: portfolio },
      { provide: FundStore, useValue: { loadFund: () => of(null) } },
      { provide: TickerProfileStore, useValue: { fetchNames: () => of({}) } },
    ],
  });
  // Strip the real child components (app-shell, kpi tiles, hero…) so only the
  // dashboard template's own loading/empty branches are under test.
  TestBed.overrideComponent(DashboardPage, {
    set: { imports: [CommonModule, RouterLink], schemas: [CUSTOM_ELEMENTS_SCHEMA] },
  });
  const f = TestBed.createComponent(DashboardPage);
  f.detectChanges();
  return f;
}

describe('F — dashboard KPI strip: net-exposure bias threshold uses the wrong unit', () => {
  it('evidence: net_exposure_pct is a PERCENT (backend valuation.py ×100) but the "balanced" band is ±0.05', () => {
    const f = setup({ portfolioFails: false });
    const page = f.componentInstance;
    // 0.5 % net long — for any practical purpose a balanced book…
    expect(page.netBias('0.5')).toBe('long-biased');
    // …only |net| ≤ 0.05 % is called balanced.
    expect(page.netBias('0.05')).toBe('balanced');
    expect(page.netBias('0.06')).toBe('long-biased');
    expect(page.netBias('-0.06')).toBe('short-biased');
  });

  it.fails('F: a book within ±5 % net should read "balanced"', () => {
    const f = setup({ portfolioFails: false });
    expect(f.componentInstance.netBias('2.5')).toBe('balanced');
  });
});

describe('F — dashboard KPI strip never leaves the loading state when /portfolio/ fails', () => {
  it('evidence: after loadOverview() errors, the 5 aria-busy skeleton tiles are still rendered and portfolioLoaded is unused by the template', () => {
    const f = setup({ portfolioFails: true });
    const page = f.componentInstance;
    expect(page.portfolioLoaded()).toBe(true); // the flag flips…
    f.detectChanges();
    const skeletons = f.nativeElement.querySelectorAll(
      'section.placeholder[aria-busy="true"][aria-label="Loading Manual Book"]',
    );
    expect(skeletons.length).toBe(5); // …but the template still shows "loading"
    expect(f.nativeElement.querySelector('[role="alert"]')).toBeNull(); // and no error is surfaced
  });

  it.fails('F: a failed portfolio load must not be rendered as an endless loading skeleton', () => {
    const f = setup({ portfolioFails: true });
    f.detectChanges();
    const skeletons = f.nativeElement.querySelectorAll('section.placeholder[aria-busy="true"]');
    expect(skeletons.length).toBe(0);
  });
});
