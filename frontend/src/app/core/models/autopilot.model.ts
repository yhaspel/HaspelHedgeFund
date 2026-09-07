// P7 — autopilot + fund view models. Numeric fields arrive as strings from
// DRF (DecimalField) — parse with +x in templates/math.

export interface AutopilotValidationCheck {
  key: string;
  ok: boolean;
  detail: string;
}

export interface AutopilotValidation {
  passed: boolean;
  checks: AutopilotValidationCheck[];
  backtest_id: number | null;
}

export interface Autopilot {
  strategy_id: number;
  is_enabled: boolean;
  state: 'active' | 'soft_cut' | 'halted';
  cron_expression: string;
  cron_description: string;
  timezone: string;
  is_market_aware: boolean;
  broker_account_id: number | null;
  model_preset: string;
  cost_ceiling_usd: string | null;
  on_breach: string;
  target_vol_pct: string;
  dd_soft_cut_pct: string;
  dd_hard_halt_pct: string;
  max_orders_per_day: number;
  max_notional_per_day_usd: string;
  liquidity_adv_cap_pct: string;
  short_mode: string;
  flatten_on_halt: boolean;
  peak_equity_usd: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  validation: AutopilotValidation;
  // P14 — the fund sleeve this strategy trades (null = not a fund member).
  sleeve?: AutopilotSleeve | null;
}

export interface AutopilotSleeve {
  fund_id: number;
  fund_configured: boolean;
  allocation_pct: string;
  initial_capital: string;
  portfolio_id: number;
}

export interface AutopilotResponse {
  autopilot: Autopilot | null;
  validation?: AutopilotValidation;
}

export interface AutopilotRunRow {
  id: number;
  fire_time: string;
  status: string;
  target_id: number | null;
  n_orders: number;
  submit_decision: Record<string, unknown>;
  guardrail_actions: Record<string, unknown>;
  error: string;
}

// P14 — one member strategy = one SLEEVE of the fund's shared paper account.
export interface FundMemberCard {
  strategy_id: number;
  name: string;
  kind: string;
  state: string | null;
  is_enabled: boolean;
  nav: string | null;
  peak_equity: string | null;
  rolling_sharpe: number | null;
  next_run_at: string | null;
  cron_description: string | null;
  /** IANA zone the cron is expressed in. `next_run_at` stays UTC — the card
   *  must render BOTH in this zone or the two rows disagree by hours/days. */
  timezone: string | null;
  /** Shadow-mode daily order/notional caps: computed and recorded, never
   *  blocking. Present so the UI can show what WOULD have been capped. */
  caps_shadow?: Record<string, unknown> | null;
  /** §9 gate warnings for an already-enabled autopilot (strict only for new
   *  enables), so the card can surface them without blocking. */
  validation_warnings?: string[];
  // P7 — why a member isn't live + the one next step (drives the card CTA).
  validation_passed: boolean;
  can_enable: boolean;
  setup_hint: string | null;
  // phase-09a — a linked backtest exists (any status) → Re-run vs Run verb.
  has_backtest: boolean;
  // P14 sleeve: share of the pool, what it came to, current cash/positions, P&L.
  allocation_pct: string;
  initial_capital: string;
  cash: string;
  positions_count: number;
  pnl_pct: number | null;
  sleeve_portfolio_id: number;
}
/** @deprecated P14 — kept as an alias while call sites migrate. */
export type FundAccountCard = FundMemberCard;

// P14 — the ONE shared paper account the fund trades.
export interface FundBrokerAccount {
  id: number;
  label: string;
  broker: string;
  broker_display: string;
  mode: string;
  connection_status: string;
  last_synced_at: string | null;
  cash: string;
  nav: string | null;
  positions_count: number;
  portfolio_id: number;
}

export interface FundResetReadiness {
  ready: boolean;
  reason: 'no_account' | 'positions' | 'inflight_orders' | 'no_members' | null;
  positions: number;
  inflight_orders: number;
}

export interface FundAttributionGap {
  available: boolean;
  positions: Record<string, string>;
  cash: string | null;
}

export interface FundLeavingMember {
  strategy_id: number;
  name: string;
  positions_count: number;
}

// GET /api/fund/candidates/ — pickable strategies for the roster editor.
export interface FundCandidate {
  id: number;
  name: string;
  kind: string;
  kind_display: string;
  is_active: boolean;
  is_member: boolean;
  allocation_pct: string | null;
  validation_passed: boolean;
  has_backtest: boolean;
}

// GET /api/fund/accounts/ — the user's paper accounts (Fund settings picker).
export interface FundAccountOption {
  id: number;
  label: string;
  broker: string;
  broker_display: string;
  connection_status: string;
  is_active: boolean;
  cash: string;
  positions_count: number;
  in_fund: boolean;
}

export interface FundMemberInput {
  strategy_id: number;
  allocation_pct: number | string;
}

// PUT /api/fund/members/ summary of what changed.
export interface FundMembersChange {
  added: number[];
  removed: number[];
  updated: number[];
  flattening: { strategy_id: number; orders: number }[];
  warnings: string[];
}

// 409 body when a removal is blocked by open positions.
export interface FundBlockingMember {
  strategy_id: number;
  name: string;
  positions: string[];
}

export interface FundCorrelation {
  available: boolean;
  reason?: string;
  min_sample?: number;
  have?: Record<string, number>;
  matrix?: Record<string, Record<string, number>>;
}

export interface FundOverview {
  fund_id: number;
  name: string;
  state: 'active' | 'halted';
  // P14 — a shared paper account is chosen (nothing can trade until it is).
  is_configured: boolean;
  broker_account: FundBrokerAccount | null;
  is_live: boolean;
  aggregate_nav: string;
  peak_equity: string | null;
  // Current drawdown-from-peak in % (what the halt breaker sees); null until
  // a peak is seeded. Resume rebases the peak, so this resets to ~0 on clear.
  drawdown_pct: number | null;
  fund_dd_halt_pct: string;
  members: FundMemberCard[];
  members_count: number;
  allocation_total_pct: string;
  // Account NAV − Σ sleeve NAV: legacy positions / manual tickets / drift.
  unallocated_nav: string | null;
  attribution_gap: FundAttributionGap | null;
  leaving: FundLeavingMember[];
  reset: FundResetReadiness;
  inflight_orders: number;
  correlation: FundCorrelation;
  recommendations: string[];
}

// P10 §C2 — GET /api/fund/history/: persisted NAV history with time-weighted
// (flow-adjusted) return indices + normalized SPY/QQQ overlays.
export interface FundHistoryPoint {
  date: string;
  equity: number;
  net_flow: number;
  index: number; // TWR index, base 100 — flow-immune
  spy?: number; // benchmark indices on the aggregate series only
  qqq?: number;
}

export interface FundHistoryAccount {
  strategy_id: number;
  name: string;
  kind: string;
  portfolio_id: number | null;
  points: FundHistoryPoint[];
  twr_pct: number | null;
  // §C4 realized-vs-expected strip: the validated annualized return from the
  // pod's record-of-record backtest.
  expected_ann_return_pct: number | null;
  expected_backtest_id: number | null;
}

export interface FundHistory {
  available: boolean;
  reason: string | null;
  per_account: FundHistoryAccount[];
  aggregate: { points: FundHistoryPoint[]; twr_pct: number | null };
  benchmarks: string[];
}

// P10 §B5 — GET /api/fund/composite/: the pods' stitched OOS validation curves
// combined at configurable weights vs SPY-TR / QQQ-TR.
export interface FundCompositeMember {
  strategy_id: number;
  name: string;
  kind: string;
  backtest_id: number;
  backtest_name: string;
  weight: number;
}

export interface FundCompositeSeriesStats {
  total_return_pct: number;
  annualized_return_pct: number;
  vol_annual_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  vs_spy?: { beta: number; alpha_annual_pct: number; information_ratio: number };
  vs_qqq?: { beta: number; alpha_annual_pct: number; information_ratio: number };
}

// P11 A3 — one per-calendar-year total-return row (composite + benchmarks).
export interface FundCompositeCalendarYear {
  year: number;
  composite?: number | null;
  spy?: number | null;
  qqq?: number | null;
}

export interface FundComposite {
  available: boolean;
  reason?: string;
  missing?: string[];
  members?: FundCompositeMember[];
  window?: { start: string; end: string };
  base?: number;
  // P11 A3 — echoed leverage / financing / sub-period + the calendar table.
  leverage?: number;
  financing_bps?: number;
  sub_period?: 'full' | 'post_gfc';
  points?: { date: string; composite: number; spy?: number; qqq?: number }[];
  metrics?: Record<string, FundCompositeSeriesStats>;
  calendar_years?: FundCompositeCalendarYear[];
}

export interface ExecutedBook {
  linked: boolean;
  // P14 — true when the book shown is the strategy's SLEEVE of the shared account.
  is_sleeve?: boolean;
  account_id?: number | null;
  account_label?: string | null;
  connection_status?: string | null;
  cash?: string;
  nav?: string;
  positions?: { ticker: string; quantity: string; avg_cost: string }[];
}
