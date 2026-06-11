export interface BacktestSummary {
  id: number;
  name: string;
  status: string;
  progress_pct: number;
  progress_message: string;
  universe: string[];
  start_date: string;
  end_date: string;
  created_at: string;
  finished_at: string | null;
  oos_sharpe: number | null;
  // P10 §B2: the stitched long-run OOS Sharpe (the honest headline — the
  // mean-of-folds number above runs ~0.3–0.4 higher).
  stitched_sharpe: number | null;
  total_return_pct: number | null;
  deflation: number | null;
  // P10 §B4: false for deterministic / single-candidate runs where the
  // OOS/IS ratio guards nothing — render "n/a", never a green ✓.
  deflation_meaningful: boolean;
  engine_mode: string;
  // P10 §B3: "price_only" rows predate the dividend fix (PR #50) and are
  // excluded as §9-gate evidence; badge them.
  data_era: 'price_only' | 'total_return';
  // P10 §D4: soft archive — non-null = hidden from the default list.
  archived_at: string | null;
}

// P10 §B2 — one benchmark's comparison block (vs the stitched OOS curve).
export interface BenchmarkBlock {
  total_return_pct: number;
  annualized_return_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  beta?: number;
  alpha_annual_pct?: number;
  information_ratio?: number;
}

export interface BacktestMetrics {
  total_return_pct: number;
  annualized_return_pct: number;
  sharpe: number;
  sortino: number;
  max_drawdown_pct: number;
  hit_rate: number;
  win_loss_ratio: number;
  turnover_pct: number;
  mean_is_sharpe: number;
  mean_oos_sharpe: number;
  sharpe_deflation: number;
  oos_sharpe_std: number;
  baseline_return_pct: number;
  // P10 §B1: the pre-fix (price-only, price-weighted) baseline, preserved by
  // the recompute_baselines command. null = never rewritten.
  baseline_return_pct_legacy: number | null;
  // P10 §B2: {"SPY": {...}, "QQQ": {...}} — may be {} for old/unrecomputed rows.
  benchmarks: Record<string, BenchmarkBlock>;
  per_agent_attribution: Record<string, number>;
}

export interface BacktestFold {
  id: number;
  fold_index: number;
  is_start: string;
  is_end: string;
  oos_start: string;
  oos_end: string;
  winning_config: Record<string, any>;
  is_sharpe: number;
  oos_sharpe: number;
  oos_return_pct: number;
  oos_max_drawdown_pct: number;
}

export interface BacktestDetail extends BacktestSummary {
  starting_cash: number;
  commission_bps: number;
  spread_bps: number;
  rebalance_frequency: string;
  is_window_days: number;
  oos_window_days: number;
  step_days: number;
  n_candidates: number;
  is_objective: string;
  baseline: string;
  error_message: string;
  total_cost_usd: number;
  max_budget_usd: number;
  metrics: BacktestMetrics | null;
  folds: BacktestFold[];
}

export interface CreateBacktestRequest {
  name: string;
  universe: string[];
  start_date: string;
  end_date: string;
  starting_cash?: number;
  commission_bps?: number;
  spread_bps?: number;
  personas?: string[];
  model_overrides?: Record<string, string>;
  rebalance_frequency?: 'daily' | 'weekly' | 'monthly';
  is_window_days: number;
  oos_window_days: number;
  step_days: number;
  n_candidates: number;
  is_objective: 'sharpe' | 'sortino' | 'calmar';
  baseline: 'universe_ew' | 'spy';
  max_budget_usd?: number;
  disable_cio?: boolean;
  // P4c: backtest on a saved agent-graph version (its models + personas win).
  graph_version_id?: number | null;
  // P7 §9: link this backtest to a strategy so completing it unlocks that
  // strategy's autopilot enable gate.
  strategy_id?: number | null;
}

export interface EstimateRequest {
  universe: string[];
  start_date: string;
  end_date: string;
  rebalance_frequency?: 'daily' | 'weekly' | 'monthly';
  personas?: string[];
  model_overrides?: Record<string, string>;
  max_budget_usd?: number;
}

export interface EstimateResponse {
  n_trading_days: number;
  n_rebalance_days: number;
  n_universe: number;
  n_invocations: number;
  n_llm_calls: number;
  est_total_usd: number;
  est_minutes_optimistic: number;
  est_minutes_upper: number;
  budget_cap_usd: number | null;
  exceeds_budget: boolean;
  by_agent: { agent: string; model: string; per_call_usd: number; total_usd: number }[];
}

// phase-09a — GET /strategies/<id>/backtest-defaults/: a cheap-but-complete
// validation-run config the New Backtest page pre-fills from the strategy.
export interface StrategyBacktestDefaults {
  strategy_id: number;
  name: string;
  universe: string[];
  personas: string[];
  include_cio: boolean;
  rebalance_frequency: 'daily' | 'weekly' | 'monthly';
  starting_cash: number;
  kind: string;
}

export interface EquityPoint {
  date: string;
  portfolio_value: number;
  baseline: number | null;
  fold_id: number | null;
  // P10 §B2: SPY-TR / QQQ-TR overlays (absent when no bars in the window).
  spy?: number;
  qqq?: number;
}

// P10 §B2 — rolling ~3y Sharpe sparkline point.
export interface RollingSharpePoint {
  date: string;
  sharpe: number;
}

export interface DeflationPayload {
  per_fold: { fold_index: number; is_sharpe: number; oos_sharpe: number }[];
  mean_is_sharpe: number;
  mean_oos_sharpe: number;
  sharpe_deflation: number;
  oos_sharpe_std: number;
  // P10 §B4: false when the ratio guards nothing (deterministic / 1 candidate).
  deflation_meaningful: boolean;
  red_flag: boolean;
}
