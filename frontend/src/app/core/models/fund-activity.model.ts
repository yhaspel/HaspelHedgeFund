/**
 * WAVE 3 — the fund's operational record.
 *
 *  • `GET /api/fund/activity/?limit=&before=` — one merged, newest-first feed
 *    of broker orders/fills/cancels, autopilot dispatches, halts/resumes/
 *    resets/flattens, guardrail transitions and sleeve reallocations. Cursor
 *    pagination: pass the previous page's `next_before` back as `before`.
 *  • `GET /api/fund/scheduler-health/` — is Celery beat actually dispatching?
 *
 * Verified against `apps/portfolios/fund_activity.py`,
 * `apps/portfolios/scheduler_health.py` and `apps/portfolios/api_fund.py`.
 * Two shapes are wider than the written contract:
 *   • `last_beat_tick` is ALWAYS an object (with `at: null` + a `detail`
 *     explaining that nothing has ever dispatched) — never `null`.
 *   • the no-fund scheduler-health body is `{available:false, reason}` only —
 *     no `autopilots`, no `warnings` — hence the optional fields below.
 */

export type FundActivitySeverity = 'info' | 'warn' | 'error';

export type FundActivityKind =
  | 'order_pending_open'
  | 'order_submitted'
  | 'order_filled'
  | 'order_cancelled'
  | 'order_rejected'
  | 'fund_flatten'
  | 'autopilot_run'
  | 'orders_released'
  | 'guardrail_transition'
  | 'fund_reset'
  | 'sleeve_reallocation'
  | 'fund_halt'
  | 'fund_resume';

export interface FundActivityLinks {
  strategy_id?: number;
  run_id?: number;
  order_id?: number;
}

export interface FundActivityEntry {
  at: string;
  kind: FundActivityKind | string;
  severity: FundActivitySeverity;
  title: string;
  detail: string;
  links: FundActivityLinks;
}

export interface FundActivityResponse {
  available: boolean;
  /** Only on the no-fund body. */
  reason?: string;
  fund_id?: number;
  limit?: number;
  before?: string | null;
  entries: FundActivityEntry[];
  has_more?: boolean;
  next_before?: string | null;
  notes?: string[];
}

export interface SchedulerBeatTick {
  at: string | null;
  source: string | null;
  age_seconds: number | null;
  /** false ⇒ this stamp is a real dispatch, not a per-tick heartbeat. */
  per_tick: boolean;
  detail: string;
}

export interface SchedulerAutopilotRow {
  autopilot_id: number;
  strategy_id: number;
  strategy_name: string;
  is_enabled: boolean;
  state: string;
  cron_expression: string;
  timezone: string;
  next_run_at: string | null;
  overdue_by_seconds: number | null;
  overdue: boolean;
  last_run_at: string | null;
  last_dispatch_at: string | null;
  last_dispatch_status: string | null;
  armed_without_next_run: boolean;
}

export interface SchedulerHealthResponse {
  available: boolean;
  reason?: string;
  now?: string;
  fund_id?: number;
  fund_state?: string;
  /**
   * TRI-STATE. `true` = a per-tick heartbeat is fresh. `false` = something is
   * demonstrably wrong (an overdue autopilot, or a stale per-tick stamp).
   * `null` = UNKNOWN — the only evidence is dispatch cadence, and a weekly
   * autopilot produces that once a week. Never render `null` as "dead".
   */
  beat_alive?: boolean | null;
  last_beat_tick?: SchedulerBeatTick | null;
  beat_stale_after_seconds?: number;
  overdue_grace_seconds?: number;
  autopilots?: SchedulerAutopilotRow[];
  armed_count?: number;
  overdue_count?: number;
  queue_depth?: number | null;
  queue_depth_reason?: string;
  warnings?: string[];
}

/** "3h 12m" / "just now" — for `overdue_by_seconds` and tick ages. */
export function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}
