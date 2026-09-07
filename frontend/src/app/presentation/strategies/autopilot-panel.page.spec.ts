import { describe, expect, it, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { of, throwError } from 'rxjs';
import { ActivatedRoute, convertToParamMap } from '@angular/router';

import { AutopilotPanelPage } from './autopilot-panel.page';
import { ConfirmService } from '../shared/confirm.service';
import { FundStore } from '../../abstraction/fund.store';
import { ModelsStore } from '../../abstraction/models.store';
import { Autopilot, AutopilotRunRow, ExecutedBook } from '../../core/models/autopilot.model';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

// Spies from the most recent setup() — read by the audit-wiring tests (AP-02/06).
let lastLoadHistory: ReturnType<typeof vi.fn>;
let lastLoadExecuted: ReturnType<typeof vi.fn>;
// Run now / Disable / Resume submit real paper orders, so they are confirm-gated.
let lastAsk: ReturnType<typeof vi.fn>;

function ap(overrides: Partial<Autopilot> = {}): Autopilot {
  return {
    strategy_id: 7, is_enabled: false, state: 'active',
    cron_expression: '30 16 * * 5', cron_description: 'At 04:30 PM, only on Friday',
    timezone: 'America/New_York',
    is_market_aware: true, broker_account_id: 3, model_preset: 'frugal',
    cost_ceiling_usd: null, on_breach: 'degrade', target_vol_pct: '10',
    dd_soft_cut_pct: '5', dd_hard_halt_pct: '7.5', max_orders_per_day: 30,
    max_notional_per_day_usd: '50000', liquidity_adv_cap_pct: '5',
    short_mode: 'single_name', flatten_on_halt: false, peak_equity_usd: null,
    last_run_at: null, next_run_at: null,
    validation: { passed: false, checks: [
      { key: 'has_backtest', ok: false, detail: 'Run a backtest first.' },
    ], backtest_id: null },
    ...overrides,
  };
}

function setup(
  autopilot: Autopilot,
  opts: { history?: AutopilotRunRow[]; executed?: ExecutedBook | null } = {},
): Cmp {
  // The panel binds store.history / store.executed (not the loadHistory return),
  // so seed the signals directly with the rows a given test needs.
  lastLoadHistory = vi.fn(() => of({ runs: [] }));
  lastLoadExecuted = vi.fn(() => of({ linked: false, positions: [], nav: null }));
  const store = {
    autopilot: signal<Autopilot | null>(autopilot).asReadonly(),
    history: signal<AutopilotRunRow[]>(opts.history ?? []).asReadonly(),
    executed: signal<ExecutedBook | null>(opts.executed ?? null).asReadonly(),
    loadAutopilot: () => of({ autopilot }),
    enable: () => of({ autopilot }),
    disable: () => of({ autopilot }),
    resume: () => of({ autopilot }),
    runNow: () => of({}),
    saveAutopilot: () => of({ autopilot }),
    loadHistory: lastLoadHistory,
    loadExecuted: lastLoadExecuted,
  } as unknown as FundStore;
  // The panel follows paramMap now, so an in-place :id change reloads.
  const route = {
    paramMap: of(convertToParamMap({ id: '7' })),
    snapshot: { paramMap: convertToParamMap({ id: '7' }) },
  } as unknown as ActivatedRoute;
  const modelsStore = {
    models: signal([]).asReadonly(),
    loadModels: () => of({ models: [] }),
    fetchPreset: () => of({ preset: 'frugal', overrides: {}, menu: [] }),
  } as unknown as ModelsStore;
  lastAsk = vi.fn(() => Promise.resolve(true));
  TestBed.configureTestingModule({
    providers: [
      { provide: FundStore, useValue: store },
      { provide: ModelsStore, useValue: modelsStore },
      { provide: ActivatedRoute, useValue: route },
      { provide: ConfirmService, useValue: { ask: lastAsk } },
    ],
  });
  const cmp = TestBed.runInInjectionContext(() => new AutopilotPanelPage());
  cmp.ngOnInit();
  return cmp;
}

describe('AutopilotPanelPage', () => {
  it('reads the strategy id from the route and hydrates the guardrail form', () => {
    const cmp = setup(ap());
    expect(cmp.strategyId).toBe(7);
    expect(cmp.form.cron_expression).toBe('30 16 * * 5');
    expect(cmp.form.dd_hard_halt_pct).toBe('7.5');
  });

  it('surfaces the validation gate (enable stays locked until it passes)', () => {
    const cmp = setup(ap());
    expect(cmp.ap().validation.passed).toBe(false);
  });

  it('shows a notice after run-now', async () => {
    const cmp = setup(ap({ is_enabled: true }));
    await cmp.runNow();
    // The action states its consequence (live paper orders) before firing.
    expect(lastAsk).toHaveBeenCalledTimes(1);
    expect(lastAsk.mock.calls[0][0].body).toContain('paper brokerage account');
    expect(cmp.notice()).toContain('queued');
  });

  it('does not run a cycle when the confirmation is declined', async () => {
    const cmp = setup(ap({ is_enabled: true }));
    lastAsk.mockImplementation(() => Promise.resolve(false));
    await cmp.runNow();
    expect(cmp.notice()).toBeNull();
  });

  it('parses an existing weekly cron into the friendly builder', () => {
    const cmp = setup(ap());                    // cron 30 16 * * 5
    expect(cmp.scheduleMode).toBe('simple');
    expect(cmp.schedFreq).toBe('weekly');
    expect(cmp.schedDays).toEqual([5]);
    expect(cmp.schedTime).toBe('16:30');
  });

  it('rebuilds the cron from weekday toggles + time', () => {
    const cmp = setup(ap());
    cmp.schedTime = '09:30';
    cmp.toggleDay(1);                           // add Monday → Mon + Fri
    expect(cmp.form.cron_expression).toBe('30 9 * * 1,5');
  });

  it('builds daily and monthly crons', () => {
    const cmp = setup(ap());
    cmp.schedTime = '17:00';
    cmp.schedFreq = 'daily'; cmp.syncCron();
    expect(cmp.form.cron_expression).toBe('0 17 * * *');
    cmp.schedFreq = 'monthly'; cmp.schedDom = 15; cmp.syncCron();
    expect(cmp.form.cron_expression).toBe('0 17 15 * *');
  });

  it('falls back to the advanced cron view when the expression is too complex', () => {
    const cmp = setup(ap({ cron_expression: '*/15 9-16 * * 1-5' }));
    expect(cmp.scheduleMode).toBe('advanced');
  });

  // AP-02 — ngOnInit fetches the run-control audit (both endpoints, once, by id).
  it('fetches the audit on init', () => {
    setup(ap());
    expect(lastLoadHistory).toHaveBeenCalledTimes(1);
    expect(lastLoadHistory).toHaveBeenCalledWith(7);
    expect(lastLoadExecuted).toHaveBeenCalledTimes(1);
    expect(lastLoadExecuted).toHaveBeenCalledWith(7);
  });

  // AP-03 — the runs binding mirrors the store.history signal.
  it('renders the seeded run-history rows', () => {
    const rows = [
      { id: 1, fire_time: '2026-06-06T16:30:00Z', status: 'submitted', target_id: null,
        n_orders: 3, submit_decision: {}, guardrail_actions: {}, error: '' },
      { id: 2, fire_time: '2026-06-05T16:30:00Z', status: 'skipped', target_id: null,
        n_orders: 0, submit_decision: {}, guardrail_actions: {}, error: '' },
    ] as AutopilotRunRow[];
    const cmp = setup(ap(), { history: rows });
    expect(cmp.runs().length).toBe(2);
  });

  // AP-04 — defensive summary helpers (verified producer keys, §2.4).
  it('summarises decisions and guardrails defensively', () => {
    const cmp = setup(ap());
    const row = (o: Partial<AutopilotRunRow>) => o as AutopilotRunRow;

    expect(cmp.decisionSummary(row({}))).toBe('—');
    expect(cmp.decisionSummary(row({ status: 'submitted', submit_decision: { submitted: 2, orders: 3 } }))).toBe('2/3 submitted');
    expect(cmp.decisionSummary(row({ status: 'submitted', submit_decision: { submitted: 0, orders: 3, market_closed: true } }))).toBe('0/3 submitted · market closed');
    expect(cmp.decisionSummary(row({ status: 'halted', submit_decision: { halted: 'drawdown' } }))).toBe('drawdown');

    expect(cmp.guardrailSummary(row({}))).toBe('—');
    expect(cmp.guardrailSummary(row({ guardrail_actions: { drawdown: { drawdown_pct: 6.2, state: 'soft_cut' } } }))).toBe('dd 6.2% · soft_cut');
    // partial-shape guard: {skipped:"no equity"} has no drawdown_pct/state.
    expect(cmp.guardrailSummary(row({ guardrail_actions: { drawdown: { skipped: 'no equity' } } }))).toBe('—');
    // 0 is a valid pct, not falsy-dropped.
    expect(cmp.guardrailSummary(row({ guardrail_actions: { drawdown: { drawdown_pct: 0, state: 'active' } } }))).toBe('dd 0% · active');
  });

  // AP-05 — the executed signal exposes the unlinked book without crashing.
  it('exposes the unlinked executed book', () => {
    const cmp = setup(ap(), { executed: { linked: false, positions: [], nav: null } as unknown as ExecutedBook });
    expect(cmp.book().linked).toBe(false);
    expect(cmp.bookError()).toBe(false);
  });

  // AP-06 — Run now refreshes the audit (loadHistory fires again) + keeps the notice.
  it('refreshes the audit after run-now', async () => {
    const cmp = setup(ap({ is_enabled: true }));
    expect(lastLoadHistory).toHaveBeenCalledTimes(1);   // ngOnInit
    await cmp.runNow();
    expect(lastLoadHistory).toHaveBeenCalledTimes(2);   // + the post-run-now refresh
    expect(cmp.notice()).toContain('queued');
  });

  it('confirms Disable and Resume, and Resume states the peak rebase', async () => {
    const cmp = setup(ap({ is_enabled: true, state: 'halted' }));
    await cmp.disable();
    expect(lastAsk.mock.calls[0][0].body).toContain('NOT flattened');
    await cmp.resume();
    expect(lastAsk.mock.calls[1][0].body).toContain('REBASED');
    expect(cmp.notice()).toContain('peak rebased');
  });

  it('surfaces the backend detail when resume is refused (halted fund → 409)', async () => {
    const autopilot = ap({ is_enabled: true, state: 'halted' });
    const store = {
      autopilot: signal<Autopilot | null>(autopilot).asReadonly(),
      history: signal<AutopilotRunRow[]>([]).asReadonly(),
      executed: signal<ExecutedBook | null>(null).asReadonly(),
      loadAutopilot: () => of({ autopilot }),
      resume: () =>
        throwError(() => ({
          status: 409,
          error: { detail: 'This strategy is a member of a halted fund — resume the fund first.' },
        })),
      loadHistory: () => of({ runs: [] }),
      loadExecuted: () => of({ linked: false, positions: [], nav: null }),
    } as unknown as FundStore;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        { provide: FundStore, useValue: store },
        {
          provide: ModelsStore,
          useValue: {
            models: signal([]).asReadonly(),
            loadModels: () => of({ models: [] }),
            fetchPreset: () => of({ preset: 'frugal', overrides: {}, menu: [] }),
          },
        },
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap({ id: '7' })),
            snapshot: { paramMap: convertToParamMap({ id: '7' }) },
          },
        },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
      ],
    });
    const cmp: Cmp = TestBed.runInInjectionContext(() => new AutopilotPanelPage());
    cmp.ngOnInit();
    await cmp.resume();
    expect(cmp.notice()).toContain('member of a halted fund');
  });
});
