import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { ScreenerStore } from '../../abstraction/screener.store';
import { ScreenResultRow } from '../../core/models/screener.model';
import { ResultsTableComponent } from './results-table.component';

/**
 * WAVE 3 F2 item 8 — screener enrichment provenance.
 *
 * A `partial` row's momentum / 52-week / moving-average fields are NULL, not
 * zero: the enrichment could not complete. Rendering those as `0.00%` sorts
 * missing data straight into the "worst momentum" bucket and makes it look
 * like a real reading, so the row is marked and the value is an em dash.
 *
 * A provider outage is a real 503 with a readable `detail` — never an empty
 * result set that reads as "your filters matched nothing".
 */

function row(patch: Partial<ScreenResultRow> = {}): ScreenResultRow {
  return {
    ticker: 'AAPL',
    name: 'Apple Inc.',
    exchange: 'NASDAQ',
    sector: 'Technology',
    industry: 'Consumer Electronics',
    is_etf: false,
    price: '231.14',
    change_pct: 0.8,
    gap_pct: 0.1,
    rvol: 1.2,
    volume: 51_000_000,
    adv_14d: 48_000_000,
    dollar_volume: 1.2e10,
    market_cap: '3400000000000',
    pe_ratio: '31.2',
    eps: '7.4',
    beta: '1.15',
    // The screener returns these as FRACTIONS; the table renders them ×100.
    momentum_1m: 0.031,
    momentum_3m: 0.084,
    momentum_6m: 0.122,
    dist_52w_high: -2.1,
    dist_52w_low: 41.0,
    above_50d_ma: true,
    above_200d_ma: true,
    has_positive_catalyst: false,
    in_watchlist: false,
    warnings: [],
    enrichment: 'cache',
    ...patch,
  };
}

const PARTIAL = row({
  ticker: 'ZZZZ',
  name: 'Thin Co.',
  enrichment: 'partial',
  momentum_1m: null,
  momentum_3m: null,
  momentum_6m: null,
  dist_52w_high: null,
  dist_52w_low: null,
  above_50d_ma: null,
  above_200d_ma: null,
  warnings: ['Price history unavailable — momentum and 52-week fields are not computed.'],
});

@Component({
  standalone: true,
  imports: [ResultsTableComponent],
  template: `<hf-screener-results-table [rows]="rows()"></hf-screener-results-table>`,
})
class Host {
  readonly rows = signal<ScreenResultRow[]>([row(), PARTIAL]);
}

describe('wave3-f2 · screener results table — partial enrichment', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Host],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
  });

  it('marks a partial row and leaves an enriched one alone', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const partialRows = el.querySelectorAll('[data-test="screener-partial-row"]');
    expect(partialRows).toHaveLength(1);
    expect(partialRows[0].textContent).toContain('ZZZZ');
    expect(el.querySelector('[data-test="screener-partial-ZZZZ"]')).not.toBeNull();
    expect(el.querySelector('[data-test="screener-partial-AAPL"]')).toBeNull();
  });

  it('carries the row warning as the pill’s explanation', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const pill = fixture.nativeElement.querySelector('[data-test="screener-partial-ZZZZ"]');
    expect(pill.getAttribute('title')).toContain('Price history unavailable');
  });

  it('renders a null momentum as "—", never as 0.00%', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const rows = Array.from(el.querySelectorAll('tbody tr')) as HTMLTableRowElement[];
    const partial = rows.find((r) => r.textContent!.includes('ZZZZ'))!;
    const enriched = rows.find((r) => r.textContent!.includes('AAPL'))!;
    // 3m % is the 10th column (index 9).
    expect(partial.cells[9].textContent!.trim()).toBe('—');
    expect(partial.cells[9].classList.contains('missing')).toBe(true);
    expect(enriched.cells[9].textContent).toContain('8.4%');
  });
});

describe('wave3-f2 · ScreenerStore — provider outage', () => {
  let store: ScreenerStore;
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
    store = TestBed.inject(ScreenerStore);
    http = TestBed.inject(HttpTestingController);
  });

  it('keeps the enrichment counts from a successful run', () => {
    store.runScreen().subscribe();
    http.expectOne((r) => r.url.endsWith('/screener/run/')).flush({
      rows: [row(), PARTIAL],
      universe_size: 500,
      enriched_count: 2,
      returned_count: 2,
      truncated: false,
      as_of: '2026-06-06T17:30:00Z',
      provider: 'fmp',
      capabilities: [],
      warnings: [],
      enrichment_counts: { cache: 1, fetched: 0, partial: 1 },
    });
    expect(store.result()!.enrichment_counts).toEqual({ cache: 1, fetched: 0, partial: 1 });
    expect(store.error()).toBeNull();
  });

  it('surfaces a 503 `detail` instead of collapsing to a generic "Run failed"', () => {
    store.runScreen().subscribe({ error: () => undefined });
    http.expectOne((r) => r.url.endsWith('/screener/run/')).flush(
      { detail: 'Market-data provider is unavailable (ReadTimeout). Try again shortly.' },
      { status: 503, statusText: 'Service Unavailable' },
    );
    expect(store.error()).toContain('Market-data provider is unavailable');
    expect(store.running()).toBe(false);
  });

  it('does not blank the previous result when a run fails — that is not "no matches"', () => {
    store.runScreen().subscribe();
    http.expectOne((r) => r.url.endsWith('/screener/run/')).flush({
      rows: [row()],
      universe_size: 500,
      enriched_count: 1,
      returned_count: 1,
      truncated: false,
      as_of: '2026-06-06T17:30:00Z',
      provider: 'fmp',
      capabilities: [],
      warnings: [],
      enrichment_counts: { cache: 1, fetched: 0, partial: 0 },
    });
    store.runScreen().subscribe({ error: () => undefined });
    http
      .expectOne((r) => r.url.endsWith('/screener/run/'))
      .flush({ detail: 'provider down' }, { status: 503, statusText: 'Service Unavailable' });
    expect(store.result()!.rows).toHaveLength(1);
    expect(store.error()).toBe('provider down');
  });
});
