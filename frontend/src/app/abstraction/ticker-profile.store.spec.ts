import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { TickerProfileStore } from './ticker-profile.store';

/**
 * Spec for the WS-2 ticker-profile cache. Covers:
 *   • TTL expiry forces a refetch (PROFILE_TTL_MS = 30 min, IDENTITY_TTL_MS = 24 h).
 *   • Concurrent fetches for the same symbol coalesce to one in-flight call.
 *   • `fetchNames` batches missing symbols into a single `/profiles/?symbols=` call
 *     and skips already-cached entries.
 *   • Synchronous `name()` / `profile()` reads return null when uncached or expired.
 */

const PROFILE_TTL_MS = 30 * 60 * 1000;
const IDENTITY_TTL_MS = 24 * 60 * 60 * 1000;

function profilePayload(ticker: string, overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ticker,
    name: `${ticker} Inc.`,
    exchange: 'NASDAQ',
    sector: 'Technology',
    price: '100.00',
    market_cap: '1000000',
    pe_ratio: '20.0',
    eps: '5.0',
    as_of: '2026-05-22',
    ...overrides,
  };
}

describe('TickerProfileStore', () => {
  let store: TickerProfileStore;
  let http: HttpTestingController;
  let now: number;

  beforeEach(() => {
    now = 1_716_400_000_000; // fixed epoch for deterministic TTL math
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(TickerProfileStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.restoreAllMocks();
  });

  describe('fetchProfile', () => {
    it('issues one GET /tickers/AAPL/profile/ for a cold ticker', () => {
      let result: { name: string } | null | undefined;
      store.fetchProfile('AAPL').subscribe((p) => { result = p; });
      http
        .expectOne((r) => r.url.endsWith('/tickers/AAPL/profile/'))
        .flush(profilePayload('AAPL'));
      expect(result?.name).toBe('AAPL Inc.');
    });

    it('uppercases lowercase tickers before the request', () => {
      store.fetchProfile('aapl').subscribe();
      http
        .expectOne((r) => r.url.endsWith('/tickers/AAPL/profile/'))
        .flush(profilePayload('AAPL'));
    });

    it('serves the cached value on a second call within the TTL', () => {
      store.fetchProfile('MSFT').subscribe();
      http.expectOne((r) => r.url.endsWith('/tickers/MSFT/profile/')).flush(profilePayload('MSFT'));

      now += PROFILE_TTL_MS - 1; // just inside the window
      let resolved: unknown;
      store.fetchProfile('MSFT').subscribe((p) => { resolved = p; });
      http.expectNone((r) => r.url.endsWith('/tickers/MSFT/profile/'));
      expect((resolved as { name: string })?.name).toBe('MSFT Inc.');
    });

    it('refetches once the entry crosses the TTL', () => {
      store.fetchProfile('NVDA').subscribe();
      http.expectOne((r) => r.url.endsWith('/tickers/NVDA/profile/')).flush(profilePayload('NVDA'));

      now += PROFILE_TTL_MS + 1; // expired
      store.fetchProfile('NVDA').subscribe();
      http.expectOne((r) => r.url.endsWith('/tickers/NVDA/profile/')).flush(profilePayload('NVDA'));
    });

    it('coalesces concurrent fetches for the same symbol into one in-flight call', () => {
      store.fetchProfile('TSLA').subscribe();
      store.fetchProfile('TSLA').subscribe();
      store.fetchProfile('TSLA').subscribe();
      // Three subscribers but only one HTTP request.
      const req = http.expectOne((r) => r.url.endsWith('/tickers/TSLA/profile/'));
      req.flush(profilePayload('TSLA'));
    });

    it('populates the identity cache when the profile resolves with a name', () => {
      store.fetchProfile('GOOG').subscribe();
      http.expectOne((r) => r.url.endsWith('/tickers/GOOG/profile/')).flush(profilePayload('GOOG'));
      expect(store.name('GOOG')).toBe('GOOG Inc.');
    });

    it('returns null on backend failure and does not populate the identity cache', () => {
      let captured: unknown;
      store.fetchProfile('FAIL').subscribe((p) => { captured = p; });
      http
        .expectOne((r) => r.url.endsWith('/tickers/FAIL/profile/'))
        .flush(null, { status: 500, statusText: 'Server Error' });
      expect(captured).toBeNull();
      expect(store.name('FAIL')).toBeNull();
    });
  });

  describe('fetchNames (batch identity)', () => {
    it('coalesces missing tickers into a single batch request', () => {
      store.fetchNames(['AAPL', 'MSFT', 'NVDA']).subscribe();
      const req = http.expectOne((r) => r.url.includes('/tickers/profiles/'));
      expect(req.request.url).toContain('symbols=AAPL%2CMSFT%2CNVDA');
      req.flush({
        profiles: {
          AAPL: { name: 'Apple Inc.', exchange: 'NASDAQ', sector: 'Technology' },
          MSFT: { name: 'Microsoft Corp.', exchange: 'NASDAQ', sector: 'Technology' },
          NVDA: { name: 'NVIDIA Corp.', exchange: 'NASDAQ', sector: 'Technology' },
        },
      });
      expect(store.name('AAPL')).toBe('Apple Inc.');
      expect(store.name('MSFT')).toBe('Microsoft Corp.');
      expect(store.name('NVDA')).toBe('NVIDIA Corp.');
    });

    it('skips already-cached tickers and fetches only the missing ones', () => {
      store.fetchNames(['AAPL']).subscribe();
      http.expectOne((r) => r.url.includes('symbols=AAPL')).flush({
        profiles: { AAPL: { name: 'Apple Inc.', exchange: 'NASDAQ', sector: 'Technology' } },
      });

      // Second call mixes a cached symbol (AAPL) with a fresh one (MSFT).
      store.fetchNames(['AAPL', 'MSFT']).subscribe();
      const req = http.expectOne((r) => r.url.includes('/tickers/profiles/'));
      expect(req.request.url).toContain('symbols=MSFT');
      expect(req.request.url).not.toContain('AAPL');
      req.flush({
        profiles: { MSFT: { name: 'Microsoft Corp.', exchange: 'NASDAQ', sector: 'Technology' } },
      });
    });

    it('returns immediately without an HTTP call when every ticker is cached', () => {
      store.fetchNames(['META']).subscribe();
      http.expectOne((r) => r.url.includes('symbols=META')).flush({
        profiles: { META: { name: 'Meta Platforms', exchange: 'NASDAQ', sector: 'Communication' } },
      });

      let projected: Record<string, { name: string }> | undefined;
      store.fetchNames(['META']).subscribe((r) => { projected = r as Record<string, { name: string }>; });
      http.expectNone((r) => r.url.includes('/tickers/profiles/'));
      expect(projected?.['META'].name).toBe('Meta Platforms');
    });

    it('coalesces overlapping concurrent batches with identical missing keys', () => {
      store.fetchNames(['AMZN', 'NFLX']).subscribe();
      store.fetchNames(['AMZN', 'NFLX']).subscribe();
      const req = http.expectOne((r) => r.url.includes('/tickers/profiles/'));
      req.flush({
        profiles: {
          AMZN: { name: 'Amazon', exchange: 'NASDAQ', sector: 'Consumer' },
          NFLX: { name: 'Netflix', exchange: 'NASDAQ', sector: 'Communication' },
        },
      });
    });

    it('expires identity cache after IDENTITY_TTL_MS', () => {
      store.fetchNames(['IBM']).subscribe();
      http.expectOne((r) => r.url.includes('symbols=IBM')).flush({
        profiles: { IBM: { name: 'IBM Corp.', exchange: 'NYSE', sector: 'Technology' } },
      });
      expect(store.name('IBM')).toBe('IBM Corp.');

      now += IDENTITY_TTL_MS + 1;
      expect(store.name('IBM')).toBeNull();

      // Next fetch sees the symbol as missing again.
      store.fetchNames(['IBM']).subscribe();
      http.expectOne((r) => r.url.includes('symbols=IBM')).flush({
        profiles: { IBM: { name: 'IBM Corp.', exchange: 'NYSE', sector: 'Technology' } },
      });
    });
  });

  describe('synchronous reads', () => {
    it('name() returns null for an uncached ticker', () => {
      expect(store.name('UNCACHED')).toBeNull();
    });

    it('profile() returns null for an uncached ticker', () => {
      expect(store.profile('UNCACHED')).toBeNull();
    });

    it('profile() returns the cached value within TTL and null past it', () => {
      store.fetchProfile('AAPL').subscribe();
      http.expectOne((r) => r.url.endsWith('/tickers/AAPL/profile/')).flush(profilePayload('AAPL'));
      expect(store.profile('AAPL')?.name).toBe('AAPL Inc.');

      now += PROFILE_TTL_MS + 1;
      expect(store.profile('AAPL')).toBeNull();
    });
  });
});
