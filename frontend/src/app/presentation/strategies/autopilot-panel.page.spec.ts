import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { of } from 'rxjs';
import { ActivatedRoute } from '@angular/router';

import { AutopilotPanelPage } from './autopilot-panel.page';
import { FundStore } from '../../abstraction/fund.store';
import { ModelsStore } from '../../abstraction/models.store';
import { Autopilot } from '../../core/models/autopilot.model';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

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

function setup(autopilot: Autopilot): Cmp {
  const store = {
    autopilot: signal<Autopilot | null>(autopilot).asReadonly(),
    loadAutopilot: () => of({ autopilot }),
    enable: () => of({ autopilot }),
    disable: () => of({ autopilot }),
    resume: () => of({ autopilot }),
    runNow: () => of({}),
    saveAutopilot: () => of({ autopilot }),
  } as unknown as FundStore;
  const route = { snapshot: { paramMap: { get: () => '7' } } } as unknown as ActivatedRoute;
  const modelsStore = {
    models: signal([]).asReadonly(),
    loadModels: () => of({ models: [] }),
    fetchPreset: () => of({ preset: 'frugal', overrides: {}, menu: [] }),
  } as unknown as ModelsStore;
  TestBed.configureTestingModule({
    providers: [
      { provide: FundStore, useValue: store },
      { provide: ModelsStore, useValue: modelsStore },
      { provide: ActivatedRoute, useValue: route },
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

  it('shows a notice after run-now', () => {
    const cmp = setup(ap({ is_enabled: true }));
    cmp.runNow();
    expect(cmp.notice()).toContain('queued');
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
});
