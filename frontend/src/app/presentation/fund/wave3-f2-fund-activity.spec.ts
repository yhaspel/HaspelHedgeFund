import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { FundStore } from '../../abstraction/fund.store';
import {
  FundActivityResponse,
  SchedulerHealthResponse,
  durationLabel,
} from '../../core/models/fund-activity.model';
import { FundActivityComponent } from './fund-activity.component';
import { SchedulerHealthComponent } from './scheduler-health.component';

/**
 * WAVE 3 F2 item 3 — the fund's operational record.
 *
 * Two behaviours are load-bearing:
 *  • the activity feed pages on a `next_before` CURSOR (entries are merged
 *    from five sources at read time, so an offset would skip rows);
 *  • `beat_alive` is TRI-STATE and `null` means UNKNOWN. Rendering `null` as
 *    "dead" would page the owner every quiet Saturday, because a weekly
 *    autopilot only produces dispatch evidence once a week.
 */

const PAGE1: FundActivityResponse = {
  available: true,
  fund_id: 1,
  limit: 40,
  before: null,
  entries: [
    {
      at: '2026-06-06T14:02:11Z',
      kind: 'order_pending_open',
      severity: 'warn',
      title: '18 orders held for the next open',
      detail: 'Market is closed; the orders are queued for 09:30 ET.',
      links: { strategy_id: 48 },
    },
    {
      at: '2026-06-05T19:44:55Z',
      kind: 'order_rejected',
      severity: 'error',
      title: 'NVDA short 12 rejected',
      detail: 'No shares available to borrow.',
      links: { order_id: 9811, run_id: 371 },
    },
    {
      at: '2026-06-04T09:30:00Z',
      kind: 'autopilot_run',
      severity: 'info',
      title: 'Autopilot cycle finished',
      detail: '',
      links: {},
    },
  ],
  has_more: true,
  next_before: '2026-06-04T09:30:00Z',
  notes: [
    'Merged read-only from broker orders/fills, autopilot run audits and the sleeve ledger.',
    'fund_halt, fund_resume, guardrail_transition are recorded forward only.',
  ],
};

const PAGE2: FundActivityResponse = {
  ...PAGE1,
  before: '2026-06-04T09:30:00Z',
  entries: [
    {
      at: '2026-06-02T21:00:00Z',
      kind: 'fund_reset',
      severity: 'info',
      title: 'Fund reset — $300,000 split across 2 sleeves',
      detail: '',
      links: {},
    },
  ],
  has_more: false,
  next_before: null,
};

function health(patch: Partial<SchedulerHealthResponse> = {}): SchedulerHealthResponse {
  return {
    available: true,
    now: '2026-06-06T17:30:00Z',
    fund_id: 1,
    fund_state: 'active',
    beat_alive: null,
    last_beat_tick: {
      at: '2026-06-06T13:58:40Z',
      source: 'AutopilotRun.started_at',
      age_seconds: 12680,
      per_tick: false,
      detail:
        'Derived from the last real dispatch, not from a per-tick heartbeat — a weekly autopilot produces this signal once a week, so its age alone does not prove beat is dead.',
    },
    beat_stale_after_seconds: 5400,
    overdue_grace_seconds: 900,
    autopilots: [
      {
        autopilot_id: 3,
        strategy_id: 48,
        strategy_name: 'Long/short core',
        is_enabled: true,
        state: 'active',
        cron_expression: '0 14 * * 1',
        timezone: 'America/New_York',
        next_run_at: '2026-06-08T18:00:00Z',
        overdue_by_seconds: null,
        overdue: false,
        last_run_at: '2026-06-01T18:00:00Z',
        last_dispatch_at: '2026-06-06T13:58:40Z',
        last_dispatch_status: 'done',
        armed_without_next_run: false,
      },
      {
        autopilot_id: 4,
        strategy_id: 56,
        strategy_name: 'Sector momentum',
        is_enabled: true,
        state: 'active',
        cron_expression: '0 13 * * 1-5',
        timezone: 'America/New_York',
        next_run_at: null,
        overdue_by_seconds: null,
        overdue: false,
        last_run_at: null,
        last_dispatch_at: null,
        last_dispatch_status: null,
        armed_without_next_run: true,
      },
    ],
    armed_count: 2,
    overdue_count: 0,
    queue_depth: null,
    queue_depth_reason:
      'Not collected: reading the Celery queue means a broker (Redis) round-trip on the request path.',
    warnings: ['Sector momentum is armed but has no next run scheduled.'],
    ...patch,
  };
}

describe('wave3-f2 · durationLabel', () => {
  it('reads as a human duration, and "—" when there is nothing to report', () => {
    expect(durationLabel(null)).toBe('—');
    expect(durationLabel(45)).toBe('45s');
    expect(durationLabel(600)).toBe('10m');
    expect(durationLabel(12680)).toBe('3h 31m');
    expect(durationLabel(300000)).toBe('3d 11h');
  });
});

describe('wave3-f2 · <hf-fund-activity>', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [FundActivityComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  function mount(body: FundActivityResponse) {
    const fixture = TestBed.createComponent(FundActivityComponent);
    fixture.detectChanges();
    http.expectOne((r) => r.url.includes('/fund/activity/')).flush(body);
    fixture.detectChanges();
    return fixture;
  }

  it('renders entries with their severity, kind and title', () => {
    const el: HTMLElement = mount(PAGE1).nativeElement;
    const rows = el.querySelectorAll('.entry');
    expect(rows).toHaveLength(3);
    expect(el.querySelector('[data-test="fund-activity-entry-order_rejected"]')!.classList).toContain(
      'error',
    );
    expect(
      el.querySelector('[data-test="fund-activity-entry-order_pending_open"]')!.classList,
    ).toContain('warn');
    expect(el.querySelector('[data-test="fund-activity-severity-error"]')!.textContent).toContain(
      'error',
    );
  });

  it('routes an entry’s links to the strategy, run and order they name', () => {
    const el: HTMLElement = mount(PAGE1).nativeElement;
    expect(
      el.querySelector('[data-test="fund-activity-link-strategy"]')!.getAttribute('href'),
    ).toBe('/strategies/48');
    expect(el.querySelector('[data-test="fund-activity-link-run"]')!.getAttribute('href')).toBe(
      '/runs/371',
    );
    expect(
      el.querySelector('[data-test="fund-activity-link-order"]')!.getAttribute('href'),
    ).toContain('/broker-accounts/pending?order=9811');
  });

  it('pages with the `next_before` cursor and closes the feed on the last page', () => {
    const fixture = mount(PAGE1);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('[data-test="fund-activity-end"]')).toBeNull();

    (el.querySelector('[data-test="fund-activity-load-more"]') as HTMLButtonElement).click();
    const req = http.expectOne((r) => r.url.includes('/fund/activity/'));
    expect(req.request.urlWithParams).toContain('before=2026-06-04T09%3A30%3A00Z');
    req.flush(PAGE2);
    fixture.detectChanges();

    expect(el.querySelectorAll('.entry')).toHaveLength(4);
    expect(el.querySelector('[data-test="fund-activity-load-more"]')).toBeNull();
    expect(el.querySelector('[data-test="fund-activity-end"]')).not.toBeNull();
  });

  it('does not duplicate an entry the cursor page repeats', () => {
    const fixture = mount(PAGE1);
    const el: HTMLElement = fixture.nativeElement;
    (el.querySelector('[data-test="fund-activity-load-more"]') as HTMLButtonElement).click();
    // A boundary row shared by both pages must appear once, not twice.
    http.expectOne((r) => r.url.includes('/fund/activity/')).flush({
      ...PAGE2,
      entries: [PAGE1.entries[2], ...PAGE2.entries],
    });
    fixture.detectChanges();
    expect(el.querySelectorAll('.entry')).toHaveLength(4);
  });

  it('renders the backend `notes` as a footnote', () => {
    const el: HTMLElement = mount(PAGE1).nativeElement;
    const notes = el.querySelector('[data-test="fund-activity-notes"]')!;
    expect(notes.querySelectorAll('li')).toHaveLength(2);
    expect(notes.textContent).toContain('recorded forward only');
  });

  it('explains the no-fund body instead of showing an empty list', () => {
    const el: HTMLElement = mount({
      available: false,
      reason: 'no fund',
      entries: [],
    }).nativeElement;
    expect(el.querySelector('[data-test="fund-activity-unavailable"]')).not.toBeNull();
    expect(el.querySelector('[data-test="fund-activity-list"]')).toBeNull();
  });

  it('renders a failed read as an error with a retry', () => {
    const fixture = TestBed.createComponent(FundActivityComponent);
    fixture.detectChanges();
    http
      .expectOne((r) => r.url.includes('/fund/activity/'))
      .flush({ detail: 'ledger unavailable' }, { status: 500, statusText: 'Error' });
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('[data-test="error-state"]')!.getAttribute('role')).toBe('alert');
    expect(el.querySelector('[data-test="error-state-detail"]')!.textContent).toContain(
      'ledger unavailable',
    );
  });
});

describe('wave3-f2 · <hf-scheduler-health>', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SchedulerHealthComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(FundStore);
  });

  function mount(body: SchedulerHealthResponse) {
    const fixture = TestBed.createComponent(SchedulerHealthComponent);
    fixture.detectChanges();
    http.expectOne((r) => r.url.includes('/fund/scheduler-health/')).flush(body);
    fixture.detectChanges();
    return fixture;
  }

  it('renders `beat_alive: null` as UNKNOWN — never as dead', () => {
    const el: HTMLElement = mount(health()).nativeElement;
    const pill = el.querySelector('[data-test="scheduler-beat"]')!;
    expect(pill.textContent).toContain('unknown');
    expect(pill.textContent!.toLowerCase()).not.toContain('dead');
    expect(pill.classList.contains('err')).toBe(false);
    expect(pill.classList.contains('warn')).toBe(true);
    // and it explains itself with the tick's own `detail`.
    expect(el.querySelector('[data-test="scheduler-tick-detail"]')!.textContent).toContain(
      'does not prove beat is dead',
    );
    expect(el.querySelector('[data-test="scheduler-beat-unknown"]')!.textContent).toContain(
      'unknown',
    );
  });

  it('renders a true heartbeat as alive and a false one as not dispatching', () => {
    let el: HTMLElement = mount(
      health({
        beat_alive: true,
        last_beat_tick: {
          at: '2026-06-06T17:20:00Z',
          source: 'django_celery_beat.PeriodicTask.last_run_at',
          age_seconds: 600,
          per_tick: true,
          detail: 'Celery beat stamps this on every dispatch.',
        },
      }),
    ).nativeElement;
    expect(el.querySelector('[data-test="scheduler-beat"]')!.textContent).toContain('alive');
    expect(el.querySelector('[data-test="scheduler-per-tick"]')!.textContent).toContain(
      'per-tick heartbeat',
    );
    expect(el.querySelector('[data-test="scheduler-beat-unknown"]')).toBeNull();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SchedulerHealthComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    el = mount(health({ beat_alive: false, overdue_count: 1 })).nativeElement;
    const pill = el.querySelector('[data-test="scheduler-beat"]')!;
    expect(pill.textContent).toContain('not dispatching');
    expect(pill.classList.contains('err')).toBe(true);
  });

  it('shows the last tick with its source and the per-tick caveat', () => {
    const el: HTMLElement = mount(health()).nativeElement;
    expect(el.querySelector('[data-test="scheduler-tick-source"]')!.textContent).toContain(
      'AutopilotRun.started_at',
    );
    expect(el.querySelector('[data-test="scheduler-per-tick"]')!.textContent).toContain(
      'dispatch cadence only',
    );
    expect(el.querySelector('[data-test="scheduler-tick-age"]')!.textContent).toContain('3h 31m');
  });

  it('badges an overdue autopilot and an armed-without-next-run one', () => {
    const h = health();
    h.autopilots![0] = {
      ...h.autopilots![0],
      overdue: true,
      overdue_by_seconds: 7500,
    };
    h.overdue_count = 1;
    const el: HTMLElement = mount(h).nativeElement;
    expect(el.querySelector('[data-test="scheduler-overdue-3"]')!.textContent).toContain(
      'overdue by 2h 5m',
    );
    expect(el.querySelector('[data-test="scheduler-nonext-4"]')!.textContent).toContain(
      'armed, no next run',
    );
    expect(el.querySelector('[data-test="scheduler-overdue"]')!.textContent).toContain('1');
  });

  it('renders the backend warnings and the queue-depth reason', () => {
    const el: HTMLElement = mount(health()).nativeElement;
    expect(el.querySelector('[data-test="scheduler-warnings"]')!.textContent).toContain(
      'no next run scheduled',
    );
    expect(el.querySelector('[data-test="scheduler-queue"]')!.textContent!.trim()).toBe('—');
    expect(el.querySelector('[data-test="scheduler-queue-reason"]')!.textContent).toContain(
      'broker (Redis) round-trip',
    );
  });

  it('handles the no-fund body (no `autopilots` key at all) without blowing up', () => {
    const el: HTMLElement = mount({
      available: false,
      reason: 'no fund',
    } as SchedulerHealthResponse).nativeElement;
    expect(el.querySelector('[data-test="scheduler-unavailable"]')!.textContent).toContain(
      'no fund',
    );
    expect(el.querySelector('[data-test="scheduler-autopilots"]')).toBeNull();
    expect(el.querySelector('[data-test="scheduler-beat"]')!.textContent).toContain('n/a');
  });
});
