/**
 * Review (fecore) — user-controlled strings interpolated into API paths
 * without encoding (contrast: TickerProfileStore / TickerHistoryStore DO
 * encodeURIComponent). The backend accepts any ≤N-char ticker on write
 * (apps/watchlists/serializers.py:14-19 only strips/uppercases), so a row
 * like "BRK/B" can be created but never removed from the UI.
 *
 * `it.fails` = confirmed defect (assertion states the correct behaviour).
 */
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { MacroStore } from './macro.store';
import { RegimeStore } from './regime.store';
import { WatchlistStore } from './watchlist.store';

describe('review-fecore: unencoded path/query interpolation', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it.fails('F: WatchlistStore.remove("BRK/B") must target the /tickers/BRK%2FB/ resource', () => {
    TestBed.inject(WatchlistStore).remove('BRK/B').subscribe({ error: () => undefined });
    const req = http.expectOne(() => true);
    expect(req.request.url.endsWith('/watchlists/default/tickers/BRK%2FB/')).toBe(true);
    req.flush(null);
  });

  it('evidence: the DELETE goes to …/tickers/BRK/B/ — a URL the backend routes nowhere (404)', () => {
    TestBed.inject(WatchlistStore).remove('BRK/B').subscribe({ error: () => undefined });
    const req = http.expectOne(() => true);
    expect(req.request.url.endsWith('/watchlists/default/tickers/BRK/B/')).toBe(true);
    req.flush({ detail: 'Not found.' }, { status: 404, statusText: 'Not Found' });
  });

  it('evidence: MacroStore / RegimeStore interpolate the raw ticker into the path too', () => {
    TestBed.inject(MacroStore).loadTickerNews('BRK/B').subscribe({ error: () => undefined });
    const r1 = http.expectOne(() => true);
    expect(r1.request.url.endsWith('/tickers/BRK/B/news/')).toBe(true);
    r1.flush({});

    TestBed.inject(RegimeStore).loadOne('BRK/B').subscribe({ error: () => undefined });
    const r2 = http.expectOne(() => true);
    expect(r2.request.url.endsWith('/macro/regime/BRK/B/')).toBe(true);
    r2.flush({});
  });
});
