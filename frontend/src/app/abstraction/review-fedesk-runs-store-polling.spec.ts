import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RunsStore } from './runs.store';
import { BacktestsStore } from './backtests.store';
import { ConfirmService } from '../presentation/shared/confirm.service';
import { OfflineState } from '../core/offline/offline-state.service';
import { environment } from '../../environments/environment';

/**
 * Review (fedesk) — polling teardown + stale-overwrite proofs for
 * RunsStore.pollRun / BacktestsStore.poll.
 *
 * Both stores clear only the *scheduled* timer in stopPolling(); a response
 * that is already in flight when the page is destroyed re-arms the timer from
 * inside its `next` handler, so polling continues after navigation.
 */
const API = environment.apiBaseUrl;

describe('review-fedesk · RunsStore.pollRun teardown', () => {
  let store: RunsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: OfflineState, useValue: { mode: () => 'online', forced: () => false } },
        { provide: ConfirmService, useValue: { notify: () => Promise.resolve() } },
      ],
    });
    store = TestBed.inject(RunsStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stopPolling();
    vi.useRealTimers();
  });

  it('keeps polling after stopPolling() when a request was in flight (leak after navigation)', () => {
    store.pollRun(7, 2000);
    const first = http.expectOne(`${API}/runs/7/`);

    // Page navigates away while the GET is still in flight → ngOnDestroy → stopPolling().
    store.stopPolling();

    // FIXED: the in-flight GET is cancelled, and the generation guard makes any
    // response that still slips through inert — so nothing re-arms the timer.
    expect(first.cancelled).toBe(true);
    expect(store.isPolling()).toBe(false);

    vi.advanceTimersByTime(2000);
    http.expectNone(`${API}/runs/7/`);
    vi.advanceTimersByTime(2000);
    http.expectNone(`${API}/runs/7/`);
  });

  it('lets a stale in-flight response for run A overwrite currentRun after pollRun(B)', () => {
    store.pollRun(1, 2000);
    const reqA = http.expectOne(`${API}/runs/1/`);

    // User opens run 2 (same component reused / new page) → pollRun(2)
    store.pollRun(2, 2000);
    // FIXED: run 1's request is cancelled and its entity dropped, so /runs/2
    // never renders run #1 while run 2 loads.
    expect(reqA.cancelled).toBe(true);
    expect(store.currentRun()).toBeNull();

    const reqB = http.expectOne(`${API}/runs/2/`);
    reqB.flush({ id: 2, status: 'done', tickers: ['MSFT'], messages: [], decisions: [], llm_calls: [] });
    expect(store.currentRun()?.id).toBe(2);

    // No second, interleaved poller for run 1.
    vi.advanceTimersByTime(2000);
    http.expectNone(`${API}/runs/1/`);
    expect(store.currentRun()?.id).toBe(2);
  });
});

describe('review-fedesk · BacktestsStore.poll teardown', () => {
  let store: BacktestsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: OfflineState, useValue: { mode: () => 'online', forced: () => false } },
        { provide: ConfirmService, useValue: { notify: () => Promise.resolve() } },
      ],
    });
    store = TestBed.inject(BacktestsStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stopPolling();
    vi.useRealTimers();
  });

  it('keeps polling a running backtest after stopPolling() when a request was in flight', () => {
    store.poll(3, 3000);
    const first = http.expectOne(`${API}/backtests/3/`);
    store.stopPolling();
    // FIXED: cancelled + generation-guarded, so polling really stops.
    expect(first.cancelled).toBe(true);
    vi.advanceTimersByTime(3000);
    http.expectNone(`${API}/backtests/3/`);
    expect(store.isPolling()).toBe(false);
  });
});
