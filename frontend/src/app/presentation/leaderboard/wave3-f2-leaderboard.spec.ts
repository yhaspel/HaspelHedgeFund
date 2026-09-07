import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { LeaderboardPage } from './leaderboard.page';

/**
 * WAVE 3 F2 item 4 — the leaderboard under the new metrics contract.
 *
 * `provisional === true` now means `sharpe`, `sortino` and
 * `annualised_return_pct` are ALL `null` — the sample cannot support a ratio.
 * The table must render "—" and explain why, must show `n_observations` (the
 * disjoint priced sample) rather than only `n_cycles`, and must keep the
 * server's provisional-last ordering instead of re-sorting it away.
 */

function strategyRow(patch: Record<string, unknown> = {}) {
  return {
    id: 1,
    strategy: 9,
    strategy_name: 'Sector momentum',
    flavor: 'sector_momentum',
    flavor_display: 'Deterministic sector momentum',
    window: '90d',
    as_of: '2026-06-06',
    n_cycles: 31,
    n_observations: 28,
    periods_per_year: '26.00',
    total_return_pct: '9.4400',
    annualised_return_pct: '11.2000',
    sharpe: '0.8400',
    sortino: null,
    sortino_note: 'fewer than 3 negative periods',
    metrics_version: 2,
    max_drawdown_pct: '6.1200',
    hit_rate: '0.6100',
    annualised_turnover_pct: '340.0000',
    avg_cost_per_cycle_usd: '0.0000',
    sharpe_p25: null,
    sharpe_p75: null,
    sortino_p25: null,
    sortino_p75: null,
    max_drawdown_p25_pct: null,
    max_drawdown_p75_pct: null,
    council_alpha_bps: null,
    council_cost_usd: '0.00',
    council_net_value_usd: null,
    baseline_version: 'v1',
    provisional: false,
    ...patch,
  };
}

const PROVISIONAL = strategyRow({
  id: 2,
  strategy: 3,
  strategy_name: 'Pairs MVP',
  flavor: 'pairs',
  flavor_display: 'Pairs trading',
  n_cycles: 4,
  n_observations: 3,
  periods_per_year: null,
  annualised_return_pct: null,
  sharpe: null,
  sortino: null,
  sortino_note: 'fewer than 20 observations',
  provisional: true,
});

function decisions(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    run_id: 100 + i,
    ticker: 'AAPL',
    as_of_date: '2026-06-0' + ((i % 9) + 1),
    signal: 'bullish',
    confidence: 0.7,
    forward_return_pct: 1.2,
  }));
}

describe('wave3-f2 · LeaderboardPage', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [LeaderboardPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  /** Boot the page, answer every load, and switch to the strategies tab. */
  function mount(rows: unknown[], agentRows: unknown[] = []) {
    const fixture = TestBed.createComponent(LeaderboardPage);
    fixture.detectChanges();
    http.expectOne((r) => r.url.includes('/leaderboard/agents/')).flush({ rows: agentRows });
    http.expectOne((r) => r.url.includes('/leaderboard/models/')).flush({ rows: [] });
    http.expectOne((r) => r.url.includes('/leaderboard/strategies/?')).flush({ rows });
    http.expectOne((r) => r.url.includes('by-flavor')).flush({ rows: [] });
    fixture.componentInstance.tab.set('strategies');
    fixture.detectChanges();
    return fixture;
  }

  it('renders a provisional row’s ratios as "—", never as a number', () => {
    const el: HTMLElement = mount([PROVISIONAL]).nativeElement;
    expect(el.querySelector('[data-test="leaderboard-sharpe"]')!.textContent!.trim()).toBe('—');
    expect(el.querySelector('[data-test="leaderboard-sortino"]')!.textContent!.trim()).toBe('—');
    const pill = el.querySelector('[data-test="leaderboard-prov"]')!;
    expect(pill.textContent).toContain('prov.');
    // The pill copy must name the real bar: measurable OBSERVATIONS, not cycles.
    expect(pill.getAttribute('title')).toContain('20 measurable observations');
    expect(pill.getAttribute('title')).toContain('3 priced holding interval');
  });

  it('shows n_observations next to n_cycles, with the observed annualisation factor', () => {
    const el: HTMLElement = mount([strategyRow()]).nativeElement;
    const obs = el.querySelector('[data-test="leaderboard-obs-sector_momentum"]')!;
    expect(obs.textContent).toContain('28');
    expect(obs.textContent).toContain('26.00/yr');
  });

  it('explains a null Sortino on a NON-provisional row with its sortino_note', () => {
    const el: HTMLElement = mount([strategyRow()]).nativeElement;
    expect(el.querySelector('[data-test="leaderboard-sortino"]')!.textContent!.trim()).toBe('—');
    expect(el.querySelector('[data-test="leaderboard-sortino-note"]')!.textContent).toContain(
      'fewer than 3 negative periods',
    );
    expect(el.querySelector('[data-test="leaderboard-sortino"]')!.getAttribute('title')).toBe(
      'fewer than 3 negative periods',
    );
    // …and the row is NOT marked provisional just because Sortino is null.
    expect(el.querySelector('[data-test="leaderboard-prov"]')).toBeNull();
  });

  it('keeps the server’s provisional-last order instead of re-sorting client-side', () => {
    // Server order: real row first, provisional last (that is the contract).
    const el: HTMLElement = mount([strategyRow(), PROVISIONAL]).nativeElement;
    const names = Array.from(el.querySelectorAll('tbody tr td:first-child')).map((c) =>
      c.textContent!.trim(),
    );
    expect(names[0]).toContain('Sector momentum');
    expect(names[1]).toContain('Pairs MVP');
  });

  it('tooltips a real ratio with the sample it actually rests on', () => {
    const el: HTMLElement = mount([strategyRow()]).nativeElement;
    expect(el.querySelector('[data-test="leaderboard-sharpe"]')!.getAttribute('title')).toContain(
      '28 disjoint priced holding interval',
    );
  });

  it('pages the agent decisions drill-down with limit/offset and stops on a short page', () => {
    const agent = {
      agent_name: 'buffett',
      model_id: 'openrouter:x',
      n_decisions: 120,
      n_directional: 100,
      hit_rate: '0.6',
      hit_rate_ci_low: '0.5',
      hit_rate_ci_high: '0.7',
      brier_score: '0.2',
      avg_forward_return_bps: '10',
      pnl_contribution_bps: null,
      n_contrarian_decisions: 0,
      contrarian_hit_rate: null,
      contrarian_hit_rate_ci_low: null,
      contrarian_hit_rate_ci_high: null,
      provisional: false,
      metrics_version: 2,
    };
    const fixture = mount([], [agent]);
    const page = fixture.componentInstance;

    page.drill('buffett');
    const first = http.expectOne((r) => r.url.includes('/decisions/'));
    expect(first.request.urlWithParams).toContain('limit=50');
    expect(first.request.urlWithParams).toContain('offset=0');
    first.flush({ decisions: decisions(50), limit: 50, offset: 0 });
    expect(page.decisions()).toHaveLength(50);
    expect(page.decisionsHasMore()).toBe(true);

    page.moreDecisions();
    const second = http.expectOne((r) => r.url.includes('/decisions/'));
    expect(second.request.urlWithParams).toContain('offset=50');
    second.flush({ decisions: decisions(7), limit: 50, offset: 50 });
    expect(page.decisions()).toHaveLength(57);
    // A short page is the last page — no more "Load more".
    expect(page.decisionsHasMore()).toBe(false);

    page.moreDecisions();
    http.expectNone((r) => r.url.includes('/decisions/'));
  });

  it('re-drilling a different agent resets the page rather than appending', () => {
    const fixture = mount([]);
    const page = fixture.componentInstance;
    page.drill('buffett');
    http.expectOne((r) => r.url.includes('/decisions/')).flush({ decisions: decisions(50) });
    page.drill('burry');
    const req = http.expectOne((r) => r.url.includes('/decisions/'));
    expect(req.request.urlWithParams).toContain('offset=0');
    req.flush({ decisions: decisions(2) });
    expect(page.decisions()).toHaveLength(2);
  });
});
