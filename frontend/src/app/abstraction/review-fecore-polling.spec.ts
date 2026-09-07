/**
 * Review (fecore) — proof tests for the polling stores (RunsStore,
 * BacktestsStore, PersonaEvolutionStore).
 *
 * `it.fails` = confirmed defect (assertion states the correct behaviour).
 */
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BacktestsStore } from './backtests.store';
import { PersonaEvolutionStore } from './persona-evolution.store';
import { RunsStore } from './runs.store';

const running = (id: number) => ({ id, status: 'running', decisions: [], messages: [] });

describe('review-fecore: RunsStore.pollRun / stopPolling', () => {
  let store: RunsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(RunsStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stopPolling();
    vi.useRealTimers();
  });

  it('F: stopPolling() while a tick is in flight must actually stop polling', () => {
    store.pollRun(7, 2000);
    const inFlight = http.expectOne((r) => r.url.endsWith('/runs/7/'));

    // The page is destroyed (ngOnDestroy → stopPolling) while the GET is in flight.
    store.stopPolling();

    // The in-flight GET is cancelled outright, so there is no late response to
    // re-arm the timer.
    expect(inFlight.cancelled).toBe(true);

    vi.advanceTimersByTime(2000);
    http.expectNone((r) => r.url.endsWith('/runs/7/'));
  });

  it('evidence: after stopPolling() a late response re-arms the poll and keeps hitting the API', () => {
    // Fixed: even if a response somehow lands after the stop (generation guard,
    // not just cancellation), nothing is re-armed and nothing is written.
    store.pollRun(7, 2000);
    const inFlight = http.expectOne((r) => r.url.endsWith('/runs/7/'));
    store.stopPolling();
    expect(inFlight.cancelled).toBe(true);

    vi.advanceTimersByTime(2000);
    http.expectNone((r) => r.url.endsWith('/runs/7/'));
    vi.advanceTimersByTime(10_000);
    http.expectNone((r) => r.url.endsWith('/runs/7/'));
    expect(store.isPolling()).toBe(false);
  });

  it('F: switching pollRun(1) → pollRun(2) must not let run 1 overwrite currentRun', () => {
    store.pollRun(1, 2000);
    const first = http.expectOne((r) => r.url.endsWith('/runs/1/'));

    // User navigates to run 2 (e.g. after "Rerun") while run 1's GET is in flight.
    store.pollRun(2, 2000);
    // Run 1's request is cancelled the moment run 2's poll starts…
    expect(first.cancelled).toBe(true);
    // …and the stale entity is cleared, so run 1 is never shown under /runs/2.
    expect(store.currentRun()).toBeNull();

    http.expectOne((r) => r.url.endsWith('/runs/2/')).flush({ ...running(2), status: 'done' });
    expect(store.currentRun()?.id).toBe(2);

    // No second, interleaved poller for run 1.
    vi.advanceTimersByTime(4000);
    http.expectNone((r) => r.url.endsWith('/runs/1/'));
    expect(store.currentRun()?.id).toBe(2);
  });

  it('evidence: isPolling is a computed over a non-signal field and never updates', () => {
    // Fixed: isPolling is a real signal now.
    expect(store.isPolling()).toBe(false);
    store.pollRun(3, 2000);
    http.expectOne((r) => r.url.endsWith('/runs/3/')).flush(running(3));
    expect(store.isPolling()).toBe(true); // a timer IS armed
    vi.advanceTimersByTime(2000);
    http.expectOne((r) => r.url.endsWith('/runs/3/')).flush({ ...running(3), status: 'done' });
    expect(store.isPolling()).toBe(false); // terminal status → polling ended
  });

  it('evidence: a single transient error stops polling silently (no retry, no error signal)', () => {
    store.pollRun(9, 2000);
    http
      .expectOne((r) => r.url.endsWith('/runs/9/'))
      .flush({ detail: 'upstream down' }, { status: 502, statusText: 'Bad Gateway' });
    vi.advanceTimersByTime(10_000);
    http.expectNone((r) => r.url.endsWith('/runs/9/'));
    expect(store.currentRun()).toBeNull();
    // Fixed: the frozen live view is now announced through pollError, which the
    // detail page renders as an error card with Retry.
    expect(store.isPolling()).toBe(false);
    expect(store.pollError()).toBe('upstream down');
  });
});

describe('review-fecore: BacktestsStore.poll / stopPolling', () => {
  let store: BacktestsStore;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(BacktestsStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stopPolling();
    vi.useRealTimers();
  });

  it('F: same in-flight race as RunsStore — stopPolling() does not cancel the pending GET', () => {
    store.poll(5, 3000);
    const inFlight = http.expectOne((r) => r.url.endsWith('/backtests/5/'));
    store.stopPolling();
    expect(inFlight.cancelled).toBe(true);
    vi.advanceTimersByTime(3000);
    http.expectNone((r) => r.url.endsWith('/backtests/5/'));
  });
});

describe('review-fecore: PersonaEvolutionStore.startPolling', () => {
  let store: PersonaEvolutionStore;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(PersonaEvolutionStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    store.stopPolling();
    vi.useRealTimers();
  });

  it('evidence: polls forever on error with no backoff and no cap (401 after logout included)', () => {
    store.startPolling();
    for (let i = 0; i < 20; i++) {
      http
        .expectOne((r) => r.url.endsWith('/persona-evolution/profiles/'))
        .flush({ detail: 'Unauthorized' }, { status: 401, statusText: 'Unauthorized' });
      vi.advanceTimersByTime(2500);
    }
    // 20 consecutive 401s → still polling every 2.5 s.
    http.expectOne((r) => r.url.endsWith('/persona-evolution/profiles/'));
  });
});
