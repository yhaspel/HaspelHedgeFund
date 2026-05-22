import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { of } from 'rxjs';

import { RunsStore } from '../../abstraction/runs.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { AuthStore } from '../../abstraction/auth.store';
import { RunsListPage } from './runs-list.page';
import { RunStatus, RunSummary } from '../../core/models/run.model';

/**
 * Spec for runs-list filter + empty-state logic.
 *
 * The page exposes two `computed` signals (`allRuns`, `filtered`) that combine
 * the runs store, source filter, and status filter. The spec verifies each
 * filter independently, the combined product, and the three template branches:
 * loading skeleton, no-runs empty state, no-match empty state.
 */

function mkRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: 1,
    tickers: ['AAPL'],
    status: 'done' as RunStatus,
    as_of_date: '2026-05-22',
    created_at: '2026-05-22T10:00:00Z',
    finished_at: '2026-05-22T10:05:00Z',
    total_cost_usd: '0.0500',
    source: 'adhoc',
    ...overrides,
  };
}

class FakeRunsStore {
  readonly _runs = signal<RunSummary[]>([]);
  readonly runs = this._runs.asReadonly();
  listRuns(_opts?: unknown) {
    return of(this._runs());
  }
}

class FakeProfileStore {
  fetchNames(_t: string[]) { return of({}); }
}

describe('RunsListPage · filter + empty-state logic', () => {
  let page: RunsListPage;
  let store: FakeRunsStore;

  beforeEach(() => {
    store = new FakeRunsStore();
    TestBed.configureTestingModule({
      imports: [RunsListPage],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: RunsStore, useValue: store },
        { provide: TickerProfileStore, useClass: FakeProfileStore },
        { provide: AuthStore, useValue: { user: signal(null), logout: () => undefined } },
      ],
    });
    const fixture = TestBed.createComponent(RunsListPage);
    page = fixture.componentInstance;
  });

  describe('allRuns', () => {
    it('returns an empty list when the store is empty', () => {
      expect(page.allRuns()).toEqual([]);
    });

    it('sorts runs by id descending', () => {
      store._runs.set([mkRun({ id: 1 }), mkRun({ id: 3 }), mkRun({ id: 2 })]);
      expect(page.allRuns().map((r) => r.id)).toEqual([3, 2, 1]);
    });
  });

  describe('source filter', () => {
    beforeEach(() => {
      store._runs.set([
        mkRun({ id: 10, source: 'adhoc' }),
        mkRun({ id: 11, source: 'strategy' }),
        mkRun({ id: 12, source: 'adhoc' }),
      ]);
    });

    it('returns everything with source=all', () => {
      expect(page.filtered().map((r) => r.id)).toEqual([12, 11, 10]);
    });

    it('restricts to manual (adhoc) runs', () => {
      page.setSourceFilter('adhoc');
      expect(page.filtered().map((r) => r.id)).toEqual([12, 10]);
    });

    it('restricts to strategy runs', () => {
      page.setSourceFilter('strategy');
      expect(page.filtered().map((r) => r.id)).toEqual([11]);
    });

    it('treats runs without an explicit source as adhoc', () => {
      store._runs.set([mkRun({ id: 20, source: undefined })]);
      page.setSourceFilter('adhoc');
      expect(page.filtered().map((r) => r.id)).toEqual([20]);
      page.setSourceFilter('strategy');
      expect(page.filtered()).toEqual([]);
    });
  });

  describe('status filter', () => {
    beforeEach(() => {
      store._runs.set([
        mkRun({ id: 30, status: 'done' }),
        mkRun({ id: 31, status: 'failed' }),
        mkRun({ id: 32, status: 'running' }),
        mkRun({ id: 33, status: 'cancelled' }),
      ]);
    });

    it('returns everything with status=all', () => {
      expect(page.filtered().length).toBe(4);
    });

    it('restricts to done', () => {
      page.setStatusFilter('done');
      expect(page.filtered().map((r) => r.id)).toEqual([30]);
    });

    it('restricts to failed', () => {
      page.setStatusFilter('failed');
      expect(page.filtered().map((r) => r.id)).toEqual([31]);
    });

    it('restricts to running', () => {
      page.setStatusFilter('running');
      expect(page.filtered().map((r) => r.id)).toEqual([32]);
    });
  });

  describe('combined filters', () => {
    beforeEach(() => {
      store._runs.set([
        mkRun({ id: 40, source: 'adhoc', status: 'done' }),
        mkRun({ id: 41, source: 'adhoc', status: 'failed' }),
        mkRun({ id: 42, source: 'strategy', status: 'done' }),
        mkRun({ id: 43, source: 'strategy', status: 'failed' }),
      ]);
    });

    it('returns the intersection of source AND status filters', () => {
      page.setSourceFilter('strategy');
      page.setStatusFilter('failed');
      expect(page.filtered().map((r) => r.id)).toEqual([43]);
    });

    it('returns an empty list when no row matches both filters', () => {
      page.setSourceFilter('strategy');
      page.setStatusFilter('running');
      expect(page.filtered()).toEqual([]);
    });
  });

  describe('resetFilters', () => {
    it('clears both filters back to "all"', () => {
      store._runs.set([mkRun({ id: 50, source: 'adhoc', status: 'done' })]);
      page.setSourceFilter('strategy');
      page.setStatusFilter('failed');
      expect(page.filtered()).toEqual([]);

      page.resetFilters();
      expect(page.sourceFilter()).toBe('all');
      expect(page.statusFilter()).toBe('all');
      expect(page.filtered().length).toBe(1);
    });
  });

  describe('empty-state branching', () => {
    it('reports zero runs when the store is empty and filtered is empty', () => {
      expect(page.allRuns().length).toBe(0);
      expect(page.filtered().length).toBe(0);
    });

    it('distinguishes "no runs at all" from "no runs match filter"', () => {
      store._runs.set([mkRun({ id: 60, source: 'adhoc', status: 'done' })]);
      // No filter → list has data.
      expect(page.allRuns().length).toBe(1);
      expect(page.filtered().length).toBe(1);

      // Now apply a filter that drops everything → allRuns nonzero, filtered zero.
      page.setStatusFilter('failed');
      expect(page.allRuns().length).toBe(1);
      expect(page.filtered().length).toBe(0);
    });
  });

  describe('loading flag', () => {
    it('starts true and flips false once listRuns settles', () => {
      expect(page.loading()).toBe(true);
      page.ngOnInit();
      expect(page.loading()).toBe(false);
    });
  });
});
