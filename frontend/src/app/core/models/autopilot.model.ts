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

export interface FundAccountCard {
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
  is_live: boolean;
  aggregate_nav: string;
  peak_equity: string | null;
  fund_dd_halt_pct: string;
  per_account: FundAccountCard[];
  correlation: FundCorrelation;
  recommendations: string[];
}

export interface ExecutedBook {
  linked: boolean;
  account_id?: number;
  account_label?: string;
  connection_status?: string;
  cash?: string;
  nav?: string;
  positions?: { ticker: string; quantity: string; avg_cost: string }[];
}
