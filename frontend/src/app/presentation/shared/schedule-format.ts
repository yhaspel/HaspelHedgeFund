/**
 * One clock face for scheduled autopilot runs.
 *
 * The bug this replaces: a member card rendered `cron_description` — produced
 * by the backend's `describe_cron()` and therefore expressed in the
 * AUTOPILOT's timezone ("At 04:30 PM, only on Friday") — right next to
 * `next_run_at | date:'EEE HH:mm'`, which Angular's DatePipe renders in the
 * BROWSER's zone with no zone label and no calendar date. A viewer in Israel
 * saw "At 04:30 PM, only on Friday" beside "Fri 23:30"; a viewer in Sydney saw
 * the weekday itself change ("Sat 06:30"). And with no date rendered, a
 * next_run_at two weeks in the past (dead scheduler) looked like "this Friday".
 *
 * These helpers render both halves in the autopilot's own zone, always with a
 * date and a zone label, and expose "is it overdue?" so the UI can say so.
 */

/** Format an instant in a specific IANA zone: "Fri 11 Sep, 16:30 EDT". */
export function formatInZone(iso: string | null | undefined, timeZone: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const opts: Intl.DateTimeFormatOptions = {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  };
  if (timeZone) opts.timeZone = timeZone;
  try {
    return new Intl.DateTimeFormat(undefined, opts).format(d);
  } catch {
    // An unknown/garbage IANA name must not blank the schedule row.
    delete opts.timeZone;
    return new Intl.DateTimeFormat(undefined, opts).format(d);
  }
}

/**
 * True when a scheduled run is in the past — the scheduler missed it (or is
 * dead). `next_run_at` is UTC, so this comparison is zone-independent.
 */
export function isOverdue(iso: string | null | undefined, now: number = Date.now()): boolean {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) && t < now;
}

/**
 * The cron description with its zone attached, so it can never be read as the
 * viewer's local wall clock: "At 04:30 PM, only on Friday (America/New_York)".
 */
export function scheduleWithZone(
  cronDescription: string | null | undefined,
  timeZone: string | null | undefined,
): string {
  const text = (cronDescription ?? '').trim();
  if (!text) return '—';
  return timeZone ? `${text} (${timeZone})` : text;
}

/** "Next run: Fri 11 Sep, 16:30 EDT" / "Overdue — was Fri 11 Sep, 16:30 EDT". */
export function nextRunLabel(
  iso: string | null | undefined,
  timeZone: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!iso) return 'No run scheduled';
  const when = formatInZone(iso, timeZone);
  return isOverdue(iso, now) ? `Overdue — was ${when}` : `Next run ${when}`;
}
