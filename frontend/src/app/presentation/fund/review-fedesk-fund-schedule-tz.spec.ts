import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  isOverdue,
  nextRunLabel,
  scheduleWithZone,
} from '../shared/schedule-format';

/**
 * Review (fedesk) — the member card rendered two clocks for one event:
 *   Schedule  = `cron_description` (backend `describe_cron()`, no timezone,
 *               interpreted in the autopilot's own zone, e.g. America/New_York)
 *   Next run  = `next_run_at | date:'EEE HH:mm'` (Angular DatePipe → the
 *               BROWSER zone, no zone label, no calendar date)
 *
 * FIXED: `FundMemberCard` now carries `timezone`, and both rows are rendered
 * through schedule-format.ts, which formats `next_run_at` (UTC) in the
 * AUTOPILOT's zone with a date + zone label, and flags an overdue fire.
 */
const proc = (globalThis as unknown as { process: { env: Record<string, string | undefined> } }).process;

const TZ = 'America/New_York';
// 16:30 America/New_York (EDT) on Fri 2026-09-11 == 20:30Z — what compute_next() stores.
const NEXT_RUN_AT = '2026-09-11T20:30:00Z';

describe('review-fedesk · fund member card schedule vs next-run clocks', () => {
  const prevTz = proc.env['TZ'];
  afterEach(() => {
    proc.env['TZ'] = prevTz;
    vi.useRealTimers();
  });

  it('shows "At 04:30 PM, only on Friday" next to "Fri 23:30" for a viewer in Israel', () => {
    proc.env['TZ'] = 'Asia/Jerusalem';
    const rendered = nextRunLabel(NEXT_RUN_AT, TZ, Date.parse('2026-09-01T00:00:00Z'));
    // One clock face: the autopilot's. Same 16:30 the cron description names.
    expect(rendered).toContain('16:30');
    expect(rendered).not.toContain('23:30');
    // …with the calendar date and an explicit zone label.
    expect(rendered).toContain('Sep');
    expect(rendered).toMatch(/\b(EDT|GMT-4)\b/);
    // …and the schedule row carries its zone so it can't be read as local time.
    expect(scheduleWithZone('At 04:30 PM, only on Friday', TZ)).toBe(
      'At 04:30 PM, only on Friday (America/New_York)',
    );
  });

  it('shows the same fire as "Sat 06:30" in Sydney — the weekday itself changes', () => {
    proc.env['TZ'] = 'Australia/Sydney';
    const rendered = nextRunLabel(NEXT_RUN_AT, TZ, Date.parse('2026-09-01T00:00:00Z'));
    // Still Friday 16:30 in the autopilot's zone, whoever is looking.
    expect(rendered).toContain('Fri');
    expect(rendered).toContain('16:30');
    expect(rendered).not.toContain('Sat');
  });

  it('cannot tell a stale next_run_at (scheduler dead) from a future one — no date is rendered', () => {
    proc.env['TZ'] = 'UTC';
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-25T12:00:00Z')); // two weeks later
    expect(isOverdue(NEXT_RUN_AT)).toBe(true);
    const rendered = nextRunLabel(NEXT_RUN_AT, TZ);
    expect(rendered).toContain('Overdue');
    expect(rendered).toContain('Sep'); // the date is rendered, so "this Friday" is impossible
    // A future fire is not flagged.
    expect(isOverdue('2026-10-02T20:30:00Z')).toBe(false);
    expect(nextRunLabel('2026-10-02T20:30:00Z', TZ)).toContain('Next run');
  });

  it('degrades safely: no next_run_at, and an unusable timezone', () => {
    expect(nextRunLabel(null, TZ)).toBe('No run scheduled');
    expect(isOverdue(null)).toBe(false);
    expect(scheduleWithZone(null, TZ)).toBe('—');
    // An unknown IANA name must not blank the row.
    expect(nextRunLabel(NEXT_RUN_AT, 'Not/AZone')).toContain('Sep');
  });
});
