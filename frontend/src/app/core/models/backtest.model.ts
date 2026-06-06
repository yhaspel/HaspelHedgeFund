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
  total_return_pct: number | null;
  deflation: number | null;
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
}

export interface DeflationPayload {
  per_fold: { fold_index: number; is_sharpe: number; oos_sharpe: number }[];
  mean_is_sharpe: number;
  mean_oos_sharpe: number;
  sharpe_deflation: number;
  oos_sharpe_std: number;
  red_flag: boolean;
}
