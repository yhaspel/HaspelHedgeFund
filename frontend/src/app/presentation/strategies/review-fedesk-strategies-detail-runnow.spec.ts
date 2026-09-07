import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import { StrategiesStore } from '../../abstraction/strategies.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { ConfirmService } from '../shared/confirm.service';
import { StrategiesDetailPage } from './strategies-detail.page';

/**
 * Review (fedesk) — "Run cycle now" on the strategy page.
 *
 * POST /strategies/<id>/run-now/ only enqueues a Celery task (202); the
 * PortfolioTarget row is created later, inside the task. The page then polls
 * refreshCycles() every 5s, but openCycle() clears the interval as soon as
 * the *newest existing* cycle is terminal — which it is, because the new row
 * doesn't exist yet. Net effect: one tick, polling stops, the notice keeps
 * saying "Refreshing every 5s." and the new cycle never appears.
 */
const DONE_CYCLE = {
  id: 41, as_of_date: '2026-09-04', status: 'done', gross_pct: '1.0000', net_pct: '1.0000',
  total_cost_usd: '0.20', created_at: '', finished_at: '2026-09-04T21:00:00Z',
  target_weights: {}, sector_exposure: {}, rejected_candidates: [], decisions: [],
  screener_ranking: null, orders: [], error_message: '',
};

class FakeStrategiesStore {
  readonly currentStrategy = signal<any>({ id: 5, name: 'Trend', kind: 'trend', cost_ceiling_per_cycle_usd: '2.00' });
  readonly cycles = signal<any[]>([DONE_CYCLE]);
  detail = vi.fn(() => of(this.currentStrategy()));
  listCycles = vi.fn(() => of([DONE_CYCLE]));
  cycleDetail = vi.fn(() => of(DONE_CYCLE));
  runNow: any = vi.fn(() => of({ task_id: 'abc', status: 'queued' }));
  estimate = vi.fn(() => of(null));
}

describe('review-fedesk · StrategiesDetailPage run-now polling', () => {
  let store: FakeStrategiesStore;
  let page: StrategiesDetailPage;

  beforeEach(() => {
    vi.useFakeTimers();
    store = new FakeStrategiesStore();
    TestBed.configureTestingModule({
      providers: [
        { provide: StrategiesStore, useValue: store },
        { provide: TickerProfileStore, useValue: { fetchNames: () => of({}), name: () => '', _bump: () => 0 } },
        { provide: GraphsStore, useValue: { loadRegistry: () => of(null), registry: signal(null) } },
        { provide: ModelsStore, useValue: { models: signal([]), loadAll: () => of({}) } },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(false) } },
        { provide: Router, useValue: { navigate: vi.fn() } },
        {
          provide: ActivatedRoute,
          useValue: {
            // The page follows paramMap now (so an in-place :id change reloads).
            paramMap: of(convertToParamMap({ id: '5' })),
            snapshot: { paramMap: convertToParamMap({ id: '5' }) },
          },
        },
      ],
    });
    page = TestBed.runInInjectionContext(() => new StrategiesDetailPage());
    page.ngOnInit();
  });

  afterEach(() => {
    page.ngOnDestroy();
    vi.useRealTimers();
  });

  it('stops polling after the first tick when the dispatched cycle has not been created yet', () => {
    page.runNow();
    expect(store.runNow).toHaveBeenCalledWith(5, {});
    expect(page.notice()).toContain('Refreshing every 5s');
    const handleAfterDispatch = (page as unknown as { pollHandle: unknown }).pollHandle;
    expect(handleAfterDispatch).not.toBeNull();

    // First 5s tick: the worker hasn't created the new PortfolioTarget yet →
    // cs[0] is still the old DONE cycle. FIXED: `awaitingDispatch` keeps the
    // interval alive until the new row shows up, so the notice stays true.
    vi.advanceTimersByTime(5000);
    expect((page as unknown as { pollHandle: unknown }).pollHandle).not.toBeNull();

    const calls = store.listCycles.mock.calls.length;
    vi.advanceTimersByTime(20_000);
    expect(store.listCycles.mock.calls.length).toBeGreaterThan(calls);
    expect(page.notice()).toContain('Refreshing every 5s');

    // …and it does not poll forever: after the watchdog window it stops and
    // says so rather than silently promising a refresh that never comes.
    vi.advanceTimersByTime(120_000);
    expect((page as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
    expect(page.notice()).toContain('has not appeared yet');
  });

  it('reports "dispatched" even though the task will return {status: "reused"} for a same-day DONE cycle', () => {
    // FIXED: the backend now answers 200 {status:"reused", target_id} when a
    // same-day DONE cycle already exists, and the page says so instead of
    // claiming a dispatch and promising a refresh.
    store.runNow = vi.fn(() => of({ status: 'reused', target_id: 41 }));
    page.runNow();
    expect(store.runNow.mock.calls[0]).toEqual([5, {}]);
    expect(page.notice()).toContain('already exists for today');
    expect(page.notice()).not.toMatch(/Refreshing every 5s/);
    expect((page as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
    expect(store.cycleDetail).toHaveBeenCalledWith(5, 41);
  });

  it('surfaces a 409 from run-now (autopilot owns the schedule) instead of a generic failure', () => {
    store.runNow = vi.fn(() =>
      throwError(() => ({
        status: 409,
        error: { detail: 'This strategy is on autopilot — use Run now on the autopilot panel.' },
      })),
    );
    page.runNow();
    expect(page.notice()).toContain('use Run now on the autopilot panel');
  });
});
