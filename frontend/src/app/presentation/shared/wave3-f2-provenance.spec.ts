import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ProvenanceStore } from '../../abstraction/provenance.store';
import { ProvenancePanelComponent } from './provenance-panel.component';
import {
  ProvenanceResponse,
  ageLabel,
  freshnessOf,
} from '../../core/models/provenance.model';

/**
 * WAVE 3 F2 item 1 — data provenance.
 *
 * The whole point of the panel is that silent staleness stops being silent:
 * a bar series whose last date is three weeks old, a regime snapshot
 * classified off a price date older than itself, a provider whose key went
 * missing. Each of those has to be *visible*, not merely present in a payload.
 */

const NOW = Date.parse('2026-06-06T17:30:00Z');

function payload(): ProvenanceResponse {
  return {
    as_of: '2026-06-06T17:30:00+00:00',
    tickers: {
      AAPL: {
        ticker: 'AAPL',
        bars: {
          last_date: '2026-06-05',
          source: 'fmp',
          count: 1258,
          adjusted_differs_from_close: true,
        },
        dividends: { last_ex_date: '2026-05-09', count: 21 },
        filings: { count: 34, newest_filed_at: '2026-05-02T20:15:00+00:00' },
        news: [
          { provider: 'fmp', newest_published_at: '2026-06-06T12:04:00+00:00', count: 118 },
        ],
        regime: {
          as_of_date: '2026-06-05',
          last_price_date: '2026-06-05',
          stale: false,
          model_type: 'markov_2state',
        },
      },
      MSFT: {
        ticker: 'MSFT',
        bars: {
          last_date: '2026-05-11',
          source: 'tiingo',
          count: 1201,
          adjusted_differs_from_close: false,
        },
        dividends: { last_ex_date: null, count: 0 },
        filings: { count: 0, newest_filed_at: null },
        news: [],
        regime: {
          as_of_date: '2026-06-05',
          last_price_date: '2026-05-11',
          stale: true,
          model_type: 'markov_2state',
        },
      },
    },
    global: {
      macro: {
        series: [
          {
            series_id: 'DGS10',
            newest_observation_date: '2026-06-04',
            newest_vintage_date: '2026-06-05',
          },
        ],
        classifier_version: 'v3',
        snapshot_as_of: '2026-06-05',
      },
      providers: {
        fmp: {
          key: 'configured',
          user_byok: true,
          freshness: { last_at: '2026-06-06T09:12:00+00:00', age_days: 0, count: 51240 },
        },
        // The LLM providers carry NO `freshness` key at all — the panel must
        // not render "last success never" for them as if data had gone stale.
        anthropic: { key: 'configured', user_byok: false },
      },
      provider_last_success: { fmp: '2026-06-06T09:12:00+00:00', anthropic: null },
      policy: { allow_platform_data_keys: false },
    },
  };
}

describe('wave3-f2 · provenance freshness helpers', () => {
  it('buckets an age into fresh / aging / stale, and unknown for a missing date', () => {
    expect(freshnessOf('2026-06-05', NOW)).toBe('fresh');
    expect(freshnessOf('2026-05-30', NOW)).toBe('aging');
    expect(freshnessOf('2026-05-11', NOW)).toBe('stale');
    expect(freshnessOf(null, NOW)).toBe('unknown');
    expect(freshnessOf('not-a-date', NOW)).toBe('unknown');
  });

  it('labels a missing timestamp "never" rather than an epoch date', () => {
    expect(ageLabel(null, NOW)).toBe('never');
    expect(ageLabel('2026-06-06T00:00:00Z', NOW)).toBe('today');
    expect(ageLabel('2026-06-05T00:00:00Z', NOW)).toBe('1d ago');
    expect(ageLabel('2026-05-11T00:00:00Z', NOW)).toBe('26d ago');
  });
});

describe('wave3-f2 · ProvenanceStore', () => {
  let store: ProvenanceStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    store = TestBed.inject(ProvenanceStore);
    http = TestBed.inject(HttpTestingController);
  });

  it('normalises, upper-cases, de-duplicates and clamps the ticker list', () => {
    expect(ProvenanceStore.normalize([' aapl ', 'AAPL', 'msft', ''])).toEqual(['AAPL', 'MSFT']);
    const many = Array.from({ length: 80 }, (_, i) => `T${i}`);
    expect(ProvenanceStore.normalize(many)).toHaveLength(50);
  });

  it('asks for exactly the tickers requested and merges rows by symbol', () => {
    store.load(['aapl', 'msft']).subscribe();
    const req = http.expectOne((r) => r.url.includes('/data/provenance/'));
    expect(req.request.urlWithParams).toContain('tickers=AAPL%2CMSFT');
    req.flush(payload());

    expect(Object.keys(store.byTicker()).sort()).toEqual(['AAPL', 'MSFT']);
    expect(store.rowsFor(['msft'])[0].bars?.source).toBe('tiingo');
    expect(store.global()?.policy.allow_platform_data_keys).toBe(false);
    expect(store.error()).toBeNull();
    http.verify();
  });

  it('omits ?tickers entirely when only the global block is wanted', () => {
    store.load([]).subscribe();
    const req = http.expectOne((r) => r.url.includes('/data/provenance/'));
    expect(req.request.urlWithParams).not.toContain('tickers=');
    req.flush(payload());
    http.verify();
  });

  it('captures a failed read on the store instead of throwing at the subscriber', () => {
    let emitted: unknown = 'not-emitted';
    store.load(['AAPL']).subscribe((v) => (emitted = v));
    http
      .expectOne((r) => r.url.includes('/data/provenance/'))
      .flush({ detail: 'upstream exploded' }, { status: 500, statusText: 'Server Error' });
    expect(emitted).toBeNull();
    expect(store.error()).toBe('upstream exploded');
    expect(store.loading()).toBe(false);
  });

  it('queues a refresh optimistically and keeps the tickers queued on success', () => {
    store.refresh(['aapl']).subscribe();
    expect(store.queued()).toEqual(['AAPL']);
    const req = http.expectOne((r) => r.url.endsWith('/data/provenance/refresh/'));
    expect(req.request.body).toEqual({ tickers: ['AAPL'] });
    req.flush({ queued: 1, task_ids: ['t-1'] });
    expect(store.queued()).toEqual(['AAPL']);
    expect(store.refreshError()).toBeNull();
    expect(store.refreshing()).toBe(false);
  });

  it('rolls the optimistic queue back and surfaces `detail` when the broker is down (503)', () => {
    store.refresh(['AAPL', 'MSFT']).subscribe({ error: () => undefined });
    expect(store.queued()).toEqual(['AAPL', 'MSFT']);
    http.expectOne((r) => r.url.endsWith('/data/provenance/refresh/')).flush(
      { detail: 'Could not queue the refresh — the task broker is unavailable (OperationalError).' },
      { status: 503, statusText: 'Service Unavailable' },
    );
    expect(store.queued()).toEqual([]);
    expect(store.refreshError()).toContain('task broker is unavailable');
    expect(store.refreshing()).toBe(false);
  });
});

describe('wave3-f2 · <hf-provenance>', () => {
  let http: HttpTestingController;

  // The age badges are computed against the wall clock, so pin it: the whole
  // point of the assertions below is WHICH bucket a date lands in.
  beforeAll(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterAll(() => {
    vi.useRealTimers();
  });

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ProvenancePanelComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  function mount(tickers: string[], showGlobal = false) {
    const fixture = TestBed.createComponent(ProvenancePanelComponent);
    fixture.componentRef.setInput('tickers', tickers);
    fixture.componentRef.setInput('showGlobal', showGlobal);
    fixture.detectChanges();
    http.expectOne((r) => r.url.includes('/data/provenance/')).flush(payload());
    fixture.detectChanges();
    return fixture;
  }

  it('shows each ticker’s last bar date, its source and an age badge', () => {
    const el: HTMLElement = mount(['AAPL', 'MSFT']).nativeElement;
    expect(el.querySelector('[data-test="provenance-row-AAPL"]')).not.toBeNull();
    expect(el.querySelector('[data-test="provenance-bar-source-AAPL"]')!.textContent).toContain(
      'fmp',
    );
    // MSFT's series stopped on 2026-05-11 — that must READ as stale.
    const badge = el.querySelector('[data-test="provenance-bar-age-MSFT"]')!;
    expect(badge.classList.contains('stale')).toBe(true);
    const fresh = el.querySelector('[data-test="provenance-bar-age-AAPL"]')!;
    expect(fresh.classList.contains('stale')).toBe(false);
  });

  it('distinguishes a total-return adjusted series from a price-only one', () => {
    const el: HTMLElement = mount(['AAPL', 'MSFT']).nativeElement;
    expect(el.querySelector('[data-test="provenance-adjusted-AAPL"]')!.textContent).toContain(
      'total-return adjusted',
    );
    expect(el.querySelector('[data-test="provenance-adjusted-MSFT"]')!.textContent).toContain(
      'price-return only',
    );
  });

  it('flags a stale regime and names the price date it was actually classified on', () => {
    const el: HTMLElement = mount(['MSFT']).nativeElement;
    expect(el.querySelector('[data-test="provenance-regime-stale-MSFT"]')).not.toBeNull();
    const regime = el.querySelector('[data-test="provenance-regime-MSFT"]')!;
    expect(regime.textContent).toContain('2026-05-11');
    expect(regime.textContent).toContain('classified 2026-06-05 off prices through 2026-05-11');
  });

  it('renders dividends, filings and per-provider news counts', () => {
    const el: HTMLElement = mount(['AAPL', 'MSFT']).nativeElement;
    expect(el.querySelector('[data-test="provenance-dividends-AAPL"]')!.textContent).toContain(
      '2026-05-09',
    );
    expect(el.querySelector('[data-test="provenance-dividends-MSFT"]')!.textContent).toContain(
      'none stored',
    );
    expect(el.querySelector('[data-test="provenance-filings-AAPL"]')!.textContent).toContain('34');
    expect(el.querySelector('[data-test="provenance-news-AAPL"]')!.textContent).toContain('fmp');
    expect(el.querySelector('[data-test="provenance-news-MSFT"]')!.textContent).toContain(
      'no stored news',
    );
  });

  it('renders the global macro + provider block, and says a provider has no freshness signal', () => {
    const el: HTMLElement = mount([], true).nativeElement;
    expect(el.querySelector('[data-test="provenance-macro-snapshot"]')!.textContent).toContain(
      '2026-06-05',
    );
    expect(el.querySelector('[data-test="provenance-macro-series"]')!.textContent).toContain(
      'DGS10',
    );
    expect(el.querySelector('[data-test="provenance-provider-fmp"]')!.textContent).toContain(
      'key configured',
    );
    // anthropic has no `freshness` key — do not imply its data went stale.
    expect(el.querySelector('[data-test="provenance-provider-anthropic"]')!.textContent).toContain(
      'no data freshness tracked',
    );
    expect(el.querySelector('[data-test="provenance-policy"]')!.textContent).toContain(
      'Platform data keys are OFF',
    );
  });

  it('queues a refresh with no confirmation step and says so', () => {
    const fixture = mount(['AAPL']);
    const el: HTMLElement = fixture.nativeElement;
    (el.querySelector('[data-test="provenance-refresh"]') as HTMLButtonElement).click();
    http
      .expectOne((r) => r.url.endsWith('/data/provenance/refresh/'))
      .flush({ queued: 1, task_ids: ['t-1'] });
    fixture.detectChanges();
    expect(el.querySelector('[data-test="provenance-queued"]')!.textContent).toContain('AAPL');
  });

  it('surfaces a refresh failure verbatim instead of a silent no-op', () => {
    const fixture = mount(['AAPL']);
    const el: HTMLElement = fixture.nativeElement;
    (el.querySelector('[data-test="provenance-refresh"]') as HTMLButtonElement).click();
    http.expectOne((r) => r.url.endsWith('/data/provenance/refresh/')).flush(
      { detail: 'the task broker is unavailable (OperationalError).' },
      { status: 503, statusText: 'Service Unavailable' },
    );
    fixture.detectChanges();
    const err = el.querySelector('[data-test="provenance-refresh-error"]')!;
    expect(err.getAttribute('role')).toBe('alert');
    expect(err.textContent).toContain('task broker is unavailable');
    expect(el.querySelector('[data-test="provenance-queued"]')).toBeNull();
  });

  it('hides "Refresh now" on the global-only card (there is no ticker to refresh)', () => {
    const el: HTMLElement = mount([], true).nativeElement;
    expect(el.querySelector('[data-test="provenance-refresh"]')).toBeNull();
  });
});
