import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthStore } from '../../abstraction/auth.store';
import { BrokerStore } from '../../abstraction/broker.store';
import { RunsStore } from '../../abstraction/runs.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import { OfflineState } from '../../core/offline/offline-state.service';
import { ConfirmService } from '../shared/confirm.service';
import { RunsDetailPage } from './runs-detail.page';

/**
 * Review (fedesk) — RunsDetailPage reads the run id ONCE from
 * `route.snapshot` in ngOnInit and never subscribes to param changes. With
 * Angular's default RouteReuseStrategy a navigation /runs/1 → /runs/2 reuses
 * the same component instance, so the page keeps showing (and polling) run
 * #1 under the /runs/2 URL. This is exactly what the page's own "↻ Rerun"
 * button does (`router.navigate(['/runs', res.id])`).
 */
class FakeRunsStore {
  readonly currentRun = signal<any>({
    id: 1, status: 'failed', tickers: ['AAPL'], as_of_date: '2026-09-01',
    created_at: '', finished_at: null, total_cost_usd: '0.1', model_overrides: {},
    personas: [], agent_versions: {}, error_message: 'boom', messages: [], decisions: [],
    llm_calls: [],
  });
  pollRun = vi.fn();
  stopPolling = vi.fn();
  cancelRun = vi.fn(() => of({ id: 1, status: 'cancelled' }));
  rerunRun = vi.fn(() => of({ id: 2, status: 'queued', rerun_of: 1 }));
}

describe('review-fedesk · RunsDetailPage ignores route param changes', () => {
  let store: FakeRunsStore;

  beforeEach(() => {
    store = new FakeRunsStore();
    TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: 'runs/:id', component: RunsDetailPage }]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: RunsStore, useValue: store },
        { provide: TickerProfileStore, useValue: { fetchNames: () => of({}), name: () => '', _bump: () => 0 } },
        { provide: BrokerStore, useValue: { accounts: signal([]), loadAccounts: () => of([]) } },
        { provide: AuthStore, useValue: { user: signal(null), logout: () => undefined } },
        { provide: OfflineState, useValue: { mode: () => 'online', forced: () => false, init: () => undefined } },
        { provide: ConfirmService, useValue: { notify: () => Promise.resolve(), active: signal(null) } },
      ],
    });
  });

  it('polls run #1 once and never starts polling run #2 after an in-place navigation', async () => {
    const harness = await RouterTestingHarness.create('/runs/1');
    const page1 = harness.routeNativeElement?.closest('hf-runs-detail');
    expect(store.pollRun).toHaveBeenCalledTimes(1);
    expect(store.pollRun).toHaveBeenCalledWith(1);

    // Same route config, different :id → DefaultRouteReuseStrategy reuses the component.
    await harness.navigateByUrl('/runs/2');
    const page2 = harness.routeNativeElement?.closest('hf-runs-detail');

    expect(page2).toBe(page1); // same DOM element / component instance
    expect(store.stopPolling).not.toHaveBeenCalled(); // ngOnDestroy never ran
    // FIXED: the page subscribes to route.paramMap, so the reused instance
    // re-reads the :id and loads run #2.
    expect(store.pollRun).toHaveBeenCalledTimes(2);
    expect(store.pollRun).toHaveBeenCalledWith(2);
  });

  it('the Rerun button navigates in place, so the new run is never loaded', async () => {
    const harness = await RouterTestingHarness.create('/runs/1');
    const page = harness.routeDebugElement!.componentInstance as RunsDetailPage;
    expect(page.canRerun()).toBe(true);

    page.rerun();
    await harness.fixture.whenStable();
    harness.detectChanges();

    expect(store.rerunRun).toHaveBeenCalledWith(1);
    expect(TestBed.inject(RunsStore) as unknown as FakeRunsStore).toBe(store);
    // URL moved to the rerun …
    const { Router } = await import('@angular/router');
    expect(TestBed.inject(Router).url).toBe('/runs/2');
    // … and the page now follows it: run #2 is fetched under the new url.
    expect(store.pollRun).toHaveBeenCalledTimes(2);
    expect(store.pollRun).toHaveBeenCalledWith(2);
  });
});
