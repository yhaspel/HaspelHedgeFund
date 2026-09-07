/**
 * Review (fecore) — RunsDetailPage reads `route.snapshot.paramMap` ONCE in
 * ngOnInit (runs-detail.page.ts:1021-1023). Angular's default RouteReuseStrategy
 * keeps the component instance when only the `:id` param changes, so a
 * same-route navigation (`rerun()` → router.navigate(['/runs', newId]),
 * runs-detail.page.ts:847) never fetches / polls the new run: the URL says
 * /runs/<new> while the page keeps showing (and polling) the OLD run.
 *
 * `it.fails` = confirmed defect (assertion states the correct behaviour).
 */
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { RunsStore } from '../../abstraction/runs.store';
import { RunsDetailPage } from './runs-detail.page';

const done = (id: number) => ({
  id,
  status: 'done',
  tickers: ['AAPL'],
  as_of_date: '2026-09-01',
  decisions: [],
  messages: [],
  agent_versions: {},
});

describe('review-fecore: RunsDetailPage same-route navigation', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([{ path: 'runs/:id', component: RunsDetailPage }]),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => TestBed.inject(RunsStore).stopPolling());

  it('F: navigating /runs/1 → /runs/2 must load run 2', async () => {
    await harness.navigateByUrl('/runs/1', RunsDetailPage);
    http.expectOne((r) => r.url.endsWith('/runs/1/')).flush(done(1));
    // Drain the page's incidental requests (ticker names, broker accounts…).
    http.match(() => true).forEach((r) => r.flush({}));

    // Same route, different :id — exactly what rerun() does.
    await TestBed.inject(Router).navigateByUrl('/runs/2');
    await harness.fixture.whenStable();

    // Correct: a GET for run 2. Actual: none — the instance was reused and
    // ngOnInit did not re-run; RunsStore.currentRun() is still run 1.
    http.expectOne((r) => r.url.endsWith('/runs/2/')).flush(done(2));
    expect(TestBed.inject(RunsStore).currentRun()?.id).toBe(2);
  });

  it('evidence: after the URL changes to /runs/2 the store still holds run 1', async () => {
    await harness.navigateByUrl('/runs/1', RunsDetailPage);
    http.expectOne((r) => r.url.endsWith('/runs/1/')).flush(done(1));
    http.match(() => true).forEach((r) => r.flush({}));

    await TestBed.inject(Router).navigateByUrl('/runs/2');
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/runs/2');
    // FIXED: the :id change is observed, run 2 is fetched, and run 1 is dropped
    // from the store immediately so it can never be shown under /runs/2.
    expect(TestBed.inject(RunsStore).currentRun()).toBeNull();
    http.expectOne((r) => r.url.endsWith('/runs/2/')).flush(done(2));
    expect(TestBed.inject(RunsStore).currentRun()?.id).toBe(2);
  });
});
