/**
 * Review (fecore) — proof tests for cross-store state issues:
 *   • CommandPaletteService.ensureLoaded() clobbers the list pages' data
 *   • AuthStore.logout() leaves other singleton stores populated
 *   • TickerProfileStore / TickerHistoryStore negative-cache transient errors
 *
 * `it.fails` = confirmed defect (assertion states the correct behaviour).
 */
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TokenStorage } from '../core/auth/token-storage';
import { AuthStore } from './auth.store';
import { CommandPaletteService } from './command-palette.service';
import { NewsStore } from './news.store';
import { PortfolioStore } from './portfolio.store';
import { RunsStore } from './runs.store';
import { StrategiesStore } from './strategies.store';
import { TickerHistoryStore } from './ticker-history.store';
import { TickerProfileStore } from './ticker-profile.store';
import { WatchlistStore } from './watchlist.store';

class FakeTokenStorage {
  private a: string | null = null;
  private r: string | null = null;
  getAccess() { return this.a; }
  getRefresh() { return this.r; }
  set(a: string, r: string) { this.a = a; this.r = r; }
  clear() { this.a = null; this.r = null; }
}

const run = (id: number) => ({ id, status: 'done', tickers: ['AAPL'], as_of_date: '2026-09-01' });

describe('review-fecore: CommandPaletteService.ensureLoaded() side effects', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it.fails('F: opening ⌘K must not replace the Runs list page rows (page 3 / all-time / search)', () => {
    const runs = TestBed.inject(RunsStore);
    // The runs list page is showing page 3 of "all time" filtered by AAPL.
    runs.listRuns({ page: 3, days: 'all', search: 'AAPL' }).subscribe();
    http
      .expectOne((r) => r.url.endsWith('/runs/?search=AAPL&days=all&page=3'))
      .flush({ count: 150, results: [run(101), run(102)] });
    expect(runs.runs().map((r) => r.id)).toEqual([101, 102]);

    // User presses ⌘K for the first time in the session.
    TestBed.inject(CommandPaletteService).ensureLoaded();
    http.expectOne((r) => r.url.endsWith('/runs/')).flush({ count: 150, results: [run(1), run(2)] });
    http.expectOne((r) => r.url.endsWith('/strategies/')).flush([]);
    http.expectOne((r) => r.url.endsWith('/backtests/')).flush({ count: 0, results: [] });

    // Correct: the page the user is looking at keeps its rows.
    // Actual: RunsStore._runs is a singleton signal; listRuns() overwrote it
    // with the default page-1 / 30-day slice → the table silently changes.
    expect(runs.runs().map((r) => r.id)).toEqual([101, 102]);
  });

  it.fails('F: opening ⌘K must not drop archived strategies from the Strategies list page', () => {
    const strategies = TestBed.inject(StrategiesStore);
    strategies.list({ includeArchived: true }).subscribe();
    http
      .expectOne((r) => r.url.endsWith('/strategies/?include_archived=1'))
      .flush([
        { id: 1, name: 'Live', kind: 'long_only', is_active: true, universe_name: 'u' },
        { id: 2, name: 'Old', kind: 'long_only', is_active: false, universe_name: 'u' },
      ]);
    expect(strategies.strategies().length).toBe(2);

    TestBed.inject(CommandPaletteService).ensureLoaded();
    http.expectOne((r) => r.url.endsWith('/runs/')).flush({ count: 0, results: [] });
    // The palette's list() omits include_archived → the server hides archived rows.
    http
      .expectOne((r) => r.url.endsWith('/strategies/'))
      .flush([{ id: 1, name: 'Live', kind: 'long_only', is_active: true, universe_name: 'u' }]);
    http.expectOne((r) => r.url.endsWith('/backtests/')).flush({ count: 0, results: [] });

    expect(strategies.strategies().length).toBe(2);
  });
});

describe('review-fecore: AuthStore.logout() and singleton store residue', () => {
  let http: HttpTestingController;
  let tokens: FakeTokenStorage;

  beforeEach(() => {
    tokens = new FakeTokenStorage();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: TokenStorage, useValue: tokens },
        { provide: Router, useValue: { navigateByUrl: () => Promise.resolve(true) } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it.fails('F: logout must clear in-memory portfolio/watchlist state (next account on this browser sees it)', () => {
    const portfolio = TestBed.inject(PortfolioStore);
    const watchlist = TestBed.inject(WatchlistStore);
    portfolio.loadOverview().subscribe();
    http.expectOne((r) => r.url.endsWith('/portfolio/')).flush({
      cash_balance: '123456.00', positions: [{ ticker: 'NVDA', quantity: '10' }],
    });
    watchlist.load().subscribe();
    http.expectOne((r) => r.url.endsWith('/watchlists/default/')).flush({
      items: [{ ticker: 'TSLA', note: 'private note' }],
    });

    tokens.set('a', 'r');
    TestBed.inject(AuthStore).logout();
    // logout() now revokes the refresh token server-side (POST /auth/logout/
    // → 205). Drain it so the afterEach http.verify() still describes the
    // in-memory residue this test is about.
    http
      .expectOne((r) => r.url.endsWith('/auth/logout/'))
      .flush(null, { status: 205, statusText: 'Reset Content' });

    // Correct: nothing of user A survives in memory. Actual (auth.store.ts:44-55):
    // only tokens/_user/IndexedDB are cleared; every other providedIn:'root'
    // store keeps user A's data until user B's page happens to refetch it.
    expect(portfolio.overview()).toBeNull();
    expect(watchlist.items()).toEqual([]);
  });
});

describe('review-fecore: NewsStore.loadPreferences() is not TTL-cached', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('evidence: every shell mount (= every route change, 34 pages) re-fetches /news/preferences/', () => {
    const news = TestBed.inject(NewsStore);
    const prefs = { chyron_enabled: false, chyron_item_count: 8 };
    // app-shell.component.ts:404-407 claims "TTL-cached + in-flight-deduped, so
    // re-mounting the shell on route change does not re-fetch" — only the
    // in-flight half is true (news.store.ts:450-469 never consults _preferences).
    news.loadPreferences().subscribe();
    http.expectOne((r) => r.url.endsWith('/news/preferences/')).flush({
      preferences: prefs, sentiment_model_choices: [], translation_model_choices: [],
    });
    news.loadPreferences().subscribe();
    http.expectOne((r) => r.url.endsWith('/news/preferences/')).flush({
      preferences: prefs, sentiment_model_choices: [], translation_model_choices: [],
    });
    news.loadPreferences().subscribe();
    http.expectOne((r) => r.url.endsWith('/news/preferences/')).flush({
      preferences: prefs, sentiment_model_choices: [], translation_model_choices: [],
    });
  });
});

describe('review-fecore: ticker stores cache transient errors as "no data"', () => {
  let http: HttpTestingController;
  let now: number;

  beforeEach(() => {
    now = 1_760_000_000_000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.restoreAllMocks();
  });

  it.fails('F: a failed batch /tickers/profiles/ call must not pin empty names for 24 h', () => {
    const store = TestBed.inject(TickerProfileStore);
    store.fetchNames(['AAPL', 'MSFT']).subscribe();
    http
      .expectOne((r) => r.url.includes('/tickers/profiles/'))
      .flush({ detail: 'upstream rate limited' }, { status: 502, statusText: 'Bad Gateway' });

    // 10 minutes later the user reloads the table (no hard refresh).
    now += 10 * 60 * 1000;
    store.fetchNames(['AAPL', 'MSFT']).subscribe();
    // Correct: retry the batch. Actual (ticker-profile.store.ts:286-292): the
    // catchError → {profiles:{}} path writes {name:''} for every symbol with a
    // 24 h TTL, so the Name column stays blank until IDENTITY_TTL_MS elapses.
    http.expectOne((r) => r.url.includes('/tickers/profiles/')).flush({ profiles: {} });
  });

  it.fails('F: a failed sparkline fetch must not be cached as an empty series for 15 min', () => {
    const store = TestBed.inject(TickerHistoryStore);
    store.fetch('AAPL').subscribe();
    http
      .expectOne((r) => r.url.includes('/tickers/AAPL/sparkline/'))
      .flush(null, { status: 503, statusText: 'Service Unavailable' });

    now += 5 * 60 * 1000;
    store.fetch('AAPL').subscribe();
    http.expectOne((r) => r.url.includes('/tickers/AAPL/sparkline/')).flush({ bars: [] });
  });

  it('evidence: after a failed profile fetch the popover shows nothing for 30 min (negative cache)', () => {
    const store = TestBed.inject(TickerProfileStore);
    store.fetchProfile('AAPL').subscribe();
    http
      .expectOne((r) => r.url.endsWith('/tickers/AAPL/profile/'))
      .flush(null, { status: 500, statusText: 'Server Error' });
    now += 29 * 60 * 1000;
    let seen: unknown = 'unset';
    store.fetchProfile('AAPL').subscribe((p) => (seen = p));
    http.expectNone((r) => r.url.endsWith('/tickers/AAPL/profile/'));
    expect(seen).toBeNull();
  });
});
