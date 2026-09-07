import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import { RunsStore } from '../../abstraction/runs.store';
import { TickerHistoryStore } from '../../abstraction/ticker-history.store';
import { RunsNewPage } from './runs-new.page';

/**
 * Review (fedesk) — RunsNewPage: no client-side ticker validation, backend
 * field errors are swallowed, and the default as-of date is the UTC date
 * rather than the user's local date.
 */
class FakeRunsStore {
  submitRun = vi.fn(() => of({ id: 1 }));
}

function build(store: FakeRunsStore, ticker = '') {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      { provide: RunsStore, useValue: store },
      { provide: ModelsStore, useValue: { loadAll: () => of({}), models: signal([]), defaultsMap: signal({}), prefs: signal(null) } },
      { provide: GraphsStore, useValue: { loadGraphs: () => of([]), graphs: signal([]) } },
      { provide: TickerHistoryStore, useValue: { fetch: () => of([]) } },
      { provide: Router, useValue: { navigate: vi.fn() } },
      { provide: ActivatedRoute, useValue: { queryParamMap: of(convertToParamMap(ticker ? { ticker } : {})), snapshot: { queryParamMap: convertToParamMap({}) } } },
    ],
  });
  return TestBed.runInInjectionContext(() => new RunsNewPage());
}

describe('review-fedesk · RunsNewPage', () => {
  let store: FakeRunsStore;
  beforeEach(() => { store = new FakeRunsStore(); });
  afterEach(() => { vi.useRealTimers(); });

  it('submits an empty / whitespace / garbage ticker without any client check', () => {
    const page = build(store);
    page.onTickerChange('   ');
    page.submit();
    // FIXED: nothing is sent; the field says what is wrong.
    expect(store.submitRun).not.toHaveBeenCalled();
    expect(page.tickerError()).toBe('Enter a ticker symbol.');

    page.onTickerChange('aapl msft'); // two symbols in the single field
    page.submit();
    expect(store.submitRun).not.toHaveBeenCalled();
    expect(page.tickerError()).toContain('One symbol only');

    page.onTickerChange('$$$');
    page.submit();
    expect(store.submitRun).not.toHaveBeenCalled();

    // A valid symbol goes through, trimmed and uppercased like the serializer does.
    page.onTickerChange('  aapl ');
    page.ticker = '  aapl ';
    page.submit();
    expect(store.submitRun).toHaveBeenCalledWith(expect.objectContaining({ tickers: ['AAPL'] }));
  });

  it('hides the backend validation message: DRF field errors have no `detail` key', () => {
    store.submitRun = vi.fn(() =>
      throwError(() => ({ status: 400, error: { tickers: ['tickers contains an empty entry'] } })),
    );
    const page = build(store);
    page.onTickerChange('AAPL');
    page.ticker = 'AAPL';
    page.submit();
    // FIXED: the serializer's own message is flattened into readable text.
    expect(page.error()).toBe('Tickers: tickers contains an empty entry');
  });

  it('flattens a multi-field DRF error and a plain {detail} alike', () => {
    store.submitRun = vi.fn(() =>
      throwError(() => ({
        status: 400,
        error: { as_of_date: ['must not be in the future'], non_field_errors: ['try again'] },
      })),
    );
    const page = build(store);
    page.onTickerChange('AAPL');
    page.ticker = 'AAPL';
    page.submit();
    expect(page.error()).toContain('As of date: must not be in the future');
    expect(page.error()).toContain('try again');
  });

  it('defaults as-of to the UTC calendar date, which is tomorrow for a US-evening user', () => {
    // 21:30 New York time on Sep 7 == 01:30 UTC on Sep 8.
    const proc = (globalThis as unknown as { process: { env: Record<string, string | undefined> } }).process;
    const prevTz = proc.env['TZ'];
    proc.env['TZ'] = 'America/New_York';
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-08T01:30:00Z'));
    try {
      const page = build(store);
      const local = new Date();
      expect(local.getDate()).toBe(7); // the user's wall-clock date is still the 7th …
      // FIXED: … and the form pre-fills that same local date.
      expect(page.asOfDate).toBe('2026-09-07');
    } finally {
      proc.env['TZ'] = prevTz;
    }
  });
});
