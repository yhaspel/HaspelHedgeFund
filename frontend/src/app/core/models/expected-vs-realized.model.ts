/**
 * WAVE 3 — `GET /api/strategies/<id>/expected-vs-realized/`.
 *
 * "The backtest said this strategy makes X ± Y per cycle. What did it actually
 * do?" Each realized holding interval is matched to the walk-forward fold that
 * covers its `as_of_date` and placed inside that fold's own out-of-sample
 * return distribution.
 *
 * The provisional contract is the whole point: while `provisional === true`
 * (no linked backtest, a failing §9 gate, uncovered cycles, or simply too few
 * scored cycles) EVERY ratio — `inside_ratio`, `outside_ratio`,
 * `mean_expected_pct`, `mean_gap_pp` — is `null` and must render as "—".
 * `mean_realized_pct` is NOT a ratio and survives (but is still null with zero
 * scored rows). Verified against `apps/portfolios/expected_vs_realized.py`.
 */

export type ExpectedSource = 'fold_daily' | 'fold_total';
export type PercentileMethod = 'empirical' | 'normal' | 'unavailable';

export interface EvrFold {
  index: number;
  id: number;
  oos_start: string;
  oos_end: string;
  oos_return_pct: number;
  oos_sharpe: number;
  /** false ⇒ nearest fold, the cycle falls OUTSIDE its window: indicative only. */
  covers: boolean;
  gap_days: number;
}

export interface EvrCycle {
  target_id: number | null;
  as_of_date: string;
  period_start: string;
  period_end: string;
  period_days: number;
  sessions_assumed: number;
  realized_return_pct: number;
  expected_return_pct: number | null;
  expected_sd_pp: number | null;
  expected_source: ExpectedSource | null;
  z_score: number | null;
  percentile: number | null;
  percentile_method: PercentileMethod;
  outside_distribution: boolean;
  sample_size: number;
  fold: EvrFold | null;
  note: string;
}

export interface EvrBacktest {
  id: number;
  name: string;
  status: string;
  engine_mode: string;
  engine_version: number;
  data_era: string;
  gate_passed: boolean;
  gate_reasons: string[];
  gate_warnings: string[];
  folds: number;
  oos_start: string | null;
  oos_end: string | null;
}

export interface EvrSummary {
  cycles: number;
  scored: number;
  unscored: number;
  z_scored: number;
  inside: number;
  outside: number;
  inside_ratio: number | null;
  outside_ratio: number | null;
  /** Null when nothing scored — it is a mean of realized rows, not a ratio. */
  mean_realized_pct: number | null;
  mean_expected_pct: number | null;
  mean_gap_pp: number | null;
  min_cycles_for_ratios: number;
  z_outside_threshold: number;
}

export interface ExpectedVsRealized {
  strategy_id: number;
  strategy_name: string;
  end_date: string;
  provisional: boolean;
  provisional_reasons: string[];
  backtest: EvrBacktest | null;
  cycles: EvrCycle[];
  summary: EvrSummary;
}
