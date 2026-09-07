/**
 * Review (feresearch) — screener proof tests.
 *
 * Convention (same as the fecore review files):
 *   `it.fails('F: …')`  = confirmed defect; the assertion states the CORRECT
 *                         behaviour and currently fails.
 *   `it('evidence: …')` = passes today and documents the behaviour the
 *                         defect rests on.
 */
import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { signal } from '@angular/core';
import { of } from 'rxjs';

import { FilterEditorComponent } from './filter-editor.component';
import { ResultsTableComponent } from './results-table.component';
import { ScreenerPage } from './screener.page';
import { ScreenerStore } from '../../abstraction/screener.store';
import { TickerProfileStore } from '../../abstraction/ticker-profile.store';
import {
  SavedScreen,
  ScreenCriterion,
  ScreenResultRow,
  ScreenerField,
} from '../../core/models/screener.model';

// Mirrors backend/apps/screener/fields.py `momentum_3m` (unit="pct").
const MOMENTUM_3M_FIELD: ScreenerField = {
  id: 'momentum_3m',
  label: '3-Month Return',
  group: 'Performance',
  kind: 'range',
  unit: 'pct',
  asset_classes: ['equity', 'etf'],
  enum_values: [],
  description: 'Price return over the last ~63 trading sessions, in percent.',
  available: true,
  requires: [],
};

function row(over: Partial<ScreenResultRow> = {}): ScreenResultRow {
  return {
    ticker: 'AAPL', name: 'Apple', exchange: 'NASDAQ', sector: 'Technology',
    industry: 'Consumer Electronics', is_etf: false, price: '100', change_pct: 1,
    gap_pct: 0, rvol: 1, volume: 1, adv_14d: 1, dollar_volume: 1,
    market_cap: '1', pe_ratio: null, eps: null, beta: null,
    momentum_1m: null, momentum_3m: null, momentum_6m: null,
    dist_52w_high: null, dist_52w_low: null, above_50d_ma: null,
    above_200d_ma: null, has_positive_catalyst: false, in_watchlist: false,
    warnings: [], ...over,
  };
}

describe('F2 — screener momentum units (backend ratio vs UI "%")', () => {
  it('evidence: the results table scales momentum_3m ×100 (0.25 → "25.0%")', () => {
    TestBed.configureTestingModule({
      providers: [{
        provide: TickerProfileStore,
        useValue: { fetchNames: () => of({}), name: () => '' },
      }],
    });
    const f = TestBed.createComponent(ResultsTableComponent);
    expect(f.componentInstance.formatPct(0.25)).toBe('25.0%');
  });

  it('evidence: the filter editor shows the preset value 0.2 next to a "%" suffix (reads as 0.2 %)', () => {
    TestBed.configureTestingModule({ imports: [FilterEditorComponent] });
    const f = TestBed.createComponent(FilterEditorComponent);
    const cmp = f.componentInstance;
    cmp.fields = [MOMENTUM_3M_FIELD];
    // Exactly what `applyPreset(momentum)` puts into the editor
    // (backend/apps/screener/presets.py: "momentum_3m": {"min": 0.20}).
    cmp.criteria = { momentum_3m: { min: 0.2 } } as Record<string, ScreenCriterion>;
    f.detectChanges();
    expect(cmp.rangeMin('momentum_3m')).toBe(0.2);
    expect(cmp.unitLabel(MOMENTUM_3M_FIELD.unit)).toBe('%');
    expect(f.nativeElement.querySelector('.unit')?.textContent?.trim()).toBe('%');
  });

  it('evidence: typing "20" in the "%" field is sent to the backend unscaled as min=20', () => {
    TestBed.configureTestingModule({ imports: [FilterEditorComponent] });
    const f = TestBed.createComponent(FilterEditorComponent);
    const cmp = f.componentInstance;
    cmp.fields = [MOMENTUM_3M_FIELD];
    cmp.criteria = {};
    let emitted: Record<string, ScreenCriterion> | null = null;
    cmp.criteriaChange.subscribe((c) => (emitted = c));
    cmp.setRange('momentum_3m', 'min', 20);
    // The backend compares this against a RATIO (0.20 == +20%), so 20 means
    // "+2000%" and matches nothing — see backend test
    // tests/test_review_feresearch_screener_units.py.
    expect(emitted).toEqual({ momentum_3m: { min: 20 } });
  });

  it.fails('F: a "%" range field must be scaled to the backend ratio (20 % → 0.20)', () => {
    TestBed.configureTestingModule({ imports: [FilterEditorComponent] });
    const f = TestBed.createComponent(FilterEditorComponent);
    const cmp = f.componentInstance;
    cmp.fields = [MOMENTUM_3M_FIELD];
    cmp.criteria = {};
    let emitted: Record<string, ScreenCriterion> | null = null;
    cmp.criteriaChange.subscribe((c) => (emitted = c));
    cmp.setRange('momentum_3m', 'min', 20);
    expect(emitted).toEqual({ momentum_3m: { min: 0.2 } });
  });
});

describe('F — saved screens cannot be deleted from the UI', () => {
  function setupPage(saved: SavedScreen[]) {
    const store = {
      fields: signal([]).asReadonly(),
      presets: signal([]).asReadonly(),
      saved: signal(saved).asReadonly(),
      watchlist: signal([]).asReadonly(),
      result: signal(null).asReadonly(),
      running: signal(false).asReadonly(),
      error: signal(null).asReadonly(),
      activeCriteria: signal({}).asReadonly(),
      activeAssetClass: signal('equity').asReadonly(),
      activeSort: signal({ field: 'market_cap', dir: 'desc' }).asReadonly(),
      activePresetId: signal('').asReadonly(),
      loadFields: () => of({ fields: [], capabilities: [] }),
      loadPresets: () => of({ presets: [] }),
      loadSaved: () => of(saved),
      loadWatchlist: () => of({ items: [], name: '' }),
      loadSavedInto: () => undefined,
    } as unknown as ScreenerStore;
    TestBed.configureTestingModule({
      providers: [
        { provide: ScreenerStore, useValue: store },
        { provide: Router, useValue: { navigate: () => Promise.resolve(true) } },
      ],
    });
    return TestBed.runInInjectionContext(() => new ScreenerPage());
  }

  const screen: SavedScreen = {
    id: 7, name: 'My momentum', asset_class: 'equity', filters: {},
    sort: { field: 'market_cap', dir: 'desc' }, based_on: '',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  };

  it('evidence: selectedSavedId stays "" after a saved screen is loaded → the Delete button (gated on selectedId) never renders', () => {
    const page = setupPage([screen]);
    page.onSavedLoaded(screen);
    expect(page.selectedSavedId()).toBe('');
  });

  it.fails('F: loading a saved screen must select it so it can be deleted', () => {
    const page = setupPage([screen]);
    page.onSavedLoaded(screen);
    expect(page.selectedSavedId()).toBe(7);
  });
});

describe('evidence — results-table column sort is local only', () => {
  it('sorting a column re-orders only the rows already returned and never updates the store sort', () => {
    TestBed.configureTestingModule({
      providers: [{
        provide: TickerProfileStore,
        useValue: { fetchNames: () => of({}), name: () => '' },
      }],
    });
    const f = TestBed.createComponent(ResultsTableComponent);
    const cmp = f.componentInstance;
    cmp.rows = [row({ ticker: 'A', momentum_3m: 0.1 }), row({ ticker: 'B', momentum_3m: 0.5 })];
    cmp.toggleSort('momentum_3m');
    expect(cmp.sortedRows().map((r) => r.ticker)).toEqual(['B', 'A']);
    // No output exists to tell the page/store about the new sort — a subsequent
    // "Run screen" still posts the previous sort (screener.store.ts runScreen()).
    expect('sortChange' in cmp).toBe(false);
  });
});
