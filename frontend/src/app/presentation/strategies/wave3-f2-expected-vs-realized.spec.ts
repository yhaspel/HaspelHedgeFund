import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { StrategiesStore } from '../../abstraction/strategies.store';
import { ExpectedVsRealized } from '../../core/models/expected-vs-realized.model';
import { ExpectedVsRealizedComponent } from './expected-vs-realized.component';

/**
 * WAVE 3 F2 item 2 — expected vs realized.
 *
 * The contract's hard rule: while `provisional === true` the backend returns
 * `null` for `inside_ratio`, `outside_ratio`, `mean_expected_pct` and
 * `mean_gap_pp`. Those must render as "—" with the reasons printed plainly —
 * a fabricated "67% inside" off three cycles is worse than no number at all.
 *
 * The second rule: `fold.covers === false` means the cycle sits outside every
 * out-of-sample window and was matched to the NEAREST fold. That row is
 * indicative only and has to say so.
 */

function evr(patch: Partial<ExpectedVsRealized> = {}): ExpectedVsRealized {
  return {
    strategy_id: 48,
    strategy_name: 'Long/short core',
    end_date: '2026-06-06',
    provisional: true,
    provisional_reasons: [
      '1 of 2 cycle(s) fall outside every out-of-sample fold window — those rows are matched to the nearest fold and are indicative only.',
      '2 scored cycle(s) — fewer than the 20 needed before a ratio means anything.',
    ],
    backtest: {
      id: 24,
      name: 'Walk-forward validation',
      status: 'done',
      engine_mode: 'council',
      engine_version: 2,
      data_era: 'total_return',
      gate_passed: true,
      gate_reasons: [],
      gate_warnings: ['Only 4 out-of-sample folds; 6 is the comfortable minimum.'],
      folds: 4,
      oos_start: '2025-06-02',
      oos_end: '2026-05-29',
    },
    cycles: [
      {
        target_id: 109,
        as_of_date: '2026-04-06',
        period_start: '2026-04-06',
        period_end: '2026-05-04',
        period_days: 28,
        sessions_assumed: 19,
        realized_return_pct: -6.83,
        expected_return_pct: 2.14,
        expected_sd_pp: 2.55,
        expected_source: 'fold_daily',
        z_score: -3.52,
        percentile: 1,
        percentile_method: 'empirical',
        outside_distribution: true,
        sample_size: 121,
        fold: {
          index: 4,
          id: 412,
          oos_start: '2026-03-02',
          oos_end: '2026-05-29',
          oos_return_pct: 4.9,
          oos_sharpe: 0.88,
          covers: true,
          gap_days: 0,
        },
        note: 'Realized -6.83% … OUTSIDE the ±2σ band the backtest described.',
      },
      {
        target_id: 110,
        as_of_date: '2026-06-01',
        period_start: '2026-06-01',
        period_end: '2026-06-06',
        period_days: 5,
        sessions_assumed: 3,
        realized_return_pct: 0.42,
        expected_return_pct: 0.31,
        expected_sd_pp: 0.94,
        expected_source: 'fold_daily',
        z_score: 0.12,
        percentile: 55,
        percentile_method: 'normal',
        outside_distribution: false,
        sample_size: 121,
        fold: {
          index: 4,
          id: 412,
          oos_start: '2026-03-02',
          oos_end: '2026-05-29',
          oos_return_pct: 4.9,
          oos_sharpe: 0.88,
          covers: false,
          gap_days: 3,
        },
        note: 'No validation fold covers 2026-06-01 — compared against the nearest fold #4.',
      },
    ],
    summary: {
      cycles: 2,
      scored: 2,
      unscored: 0,
      z_scored: 2,
      inside: 1,
      outside: 1,
      inside_ratio: null,
      outside_ratio: null,
      mean_realized_pct: -3.21,
      mean_expected_pct: null,
      mean_gap_pp: null,
      min_cycles_for_ratios: 20,
      z_outside_threshold: 2,
    },
    ...patch,
  };
}

describe('wave3-f2 · <hf-expected-vs-realized>', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ExpectedVsRealizedComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    // The store is app-wide; clear anything a previous test left behind.
    TestBed.inject(StrategiesStore).clearExpectedVsRealized();
  });

  function mount(body: ExpectedVsRealized | null, status?: { status: number; detail: string }) {
    const fixture = TestBed.createComponent(ExpectedVsRealizedComponent);
    fixture.componentRef.setInput('strategyId', 48);
    fixture.detectChanges();
    const req = http.expectOne((r) => r.url.endsWith('/strategies/48/expected-vs-realized/'));
    if (status) {
      req.flush({ detail: status.detail }, { status: status.status, statusText: 'Error' });
    } else {
      req.flush(body);
    }
    fixture.detectChanges();
    return fixture;
  }

  it('renders every withheld ratio as "—", never as 0', () => {
    const el: HTMLElement = mount(evr()).nativeElement;
    expect(el.querySelector('[data-test="evr-inside-ratio"]')!.textContent).toContain('—');
    expect(el.querySelector('[data-test="evr-outside-ratio"]')!.textContent).toContain('—');
    expect(el.querySelector('[data-test="evr-mean-expected"]')!.textContent!.trim()).toBe('—');
    expect(el.querySelector('[data-test="evr-mean-gap"]')!.textContent!.trim()).toBe('—');
    // The realized mean is NOT a ratio and survives being provisional.
    expect(el.querySelector('[data-test="evr-mean-realized"]')!.textContent).toContain('-3.21%');
  });

  it('prints the provisional reasons plainly and flags the section as provisional', () => {
    const el: HTMLElement = mount(evr()).nativeElement;
    expect(el.querySelector('[data-test="evr-provisional"]')).not.toBeNull();
    expect(el.querySelector('[data-test="evr-validated"]')).toBeNull();
    const reasons = el.querySelector('[data-test="evr-reasons"]')!;
    expect(reasons.textContent).toContain('fewer than the 20 needed');
    expect(reasons.textContent).toContain('fall outside every out-of-sample fold window');
    expect(el.querySelector('[data-test="evr-ratio-note"]')!.textContent).toContain('20 cycles');
  });

  it('shows real ratios once the answer is no longer provisional', () => {
    const done = evr({
      provisional: false,
      provisional_reasons: [],
      summary: {
        ...evr().summary,
        cycles: 24,
        scored: 24,
        z_scored: 24,
        inside: 21,
        outside: 3,
        inside_ratio: 0.875,
        outside_ratio: 0.125,
        mean_expected_pct: 1.02,
        mean_gap_pp: -0.41,
      },
    });
    const el: HTMLElement = mount(done).nativeElement;
    expect(el.querySelector('[data-test="evr-validated"]')).not.toBeNull();
    expect(el.querySelector('[data-test="evr-inside-ratio"]')!.textContent).toContain('88%');
    expect(el.querySelector('[data-test="evr-outside-ratio"]')!.textContent).toContain('13%');
    expect(el.querySelector('[data-test="evr-mean-gap"]')!.textContent).toContain('-0.41pp');
    expect(el.querySelector('[data-test="evr-reasons"]')).toBeNull();
  });

  it('marks each cycle inside or outside the band the backtest described', () => {
    const el: HTMLElement = mount(evr()).nativeElement;
    expect(el.querySelector('[data-test="evr-band-2026-04-06"]')!.textContent).toContain('outside');
    expect(el.querySelector('[data-test="evr-band-2026-04-06"]')!.classList).toContain('err');
    expect(el.querySelector('[data-test="evr-band-2026-06-01"]')!.textContent).toContain('inside');
    expect(el.querySelector('[data-test="evr-band-2026-06-01"]')!.classList).toContain('ok');
  });

  it('warns on a row matched to the NEAREST fold, and does not warn where the fold covers', () => {
    const el: HTMLElement = mount(evr()).nativeElement;
    const warn = el.querySelector('[data-test="evr-nearest-2026-06-01"]')!;
    expect(warn.textContent).toContain('nearest fold, does not cover');
    expect(warn.textContent).toContain('3d away');
    expect(el.querySelector('[data-test="evr-nearest-2026-04-06"]')).toBeNull();
    // Both rows still link to the fold's backtest.
    expect(el.querySelector('[data-test="evr-fold-2026-06-01"]')!.textContent).toContain('fold #4');
  });

  it('renders an unscored cycle as "—" for expected / z / percentile', () => {
    const unscored = evr({
      cycles: [
        {
          ...evr().cycles[0],
          as_of_date: '2026-02-02',
          expected_return_pct: null,
          expected_sd_pp: null,
          expected_source: null,
          z_score: null,
          percentile: null,
          percentile_method: 'unavailable',
          outside_distribution: false,
          sample_size: 0,
          fold: null,
          note: 'No validation backtest fold exists for this strategy.',
        },
      ],
    });
    const el: HTMLElement = mount(unscored).nativeElement;
    expect(el.querySelector('[data-test="evr-expected-2026-02-02"]')!.textContent!.trim()).toBe(
      '—',
    );
    expect(el.querySelector('[data-test="evr-band-2026-02-02"]')!.textContent).toContain(
      'unscored',
    );
  });

  it('says so plainly when no validation backtest is linked at all', () => {
    const el: HTMLElement = mount(evr({ backtest: null })).nativeElement;
    expect(el.querySelector('[data-test="evr-no-backtest"]')!.textContent).toContain(
      'No validation backtest is linked',
    );
    expect(el.querySelector('[data-test="evr-backtest"]')).toBeNull();
  });

  it('surfaces the gate state and its warnings from the linked backtest', () => {
    const el: HTMLElement = mount(evr()).nativeElement;
    expect(el.querySelector('[data-test="evr-gate"]')!.textContent).toContain('passed');
    expect(el.querySelector('[data-test="evr-gate-warnings"]')!.textContent).toContain(
      'Only 4 out-of-sample folds',
    );
  });

  it('renders a failed read as an error with a retry, not as an empty section', () => {
    const el: HTMLElement = mount(null, { status: 500, detail: 'strategy blew up' })
      .nativeElement as HTMLElement;
    const box = el.querySelector('[data-test="error-state"]')!;
    expect(box.getAttribute('role')).toBe('alert');
    expect(el.querySelector('[data-test="error-state-detail"]')!.textContent).toContain(
      'strategy blew up',
    );
    expect(el.querySelector('[data-test="evr-table"]')).toBeNull();
  });
});
