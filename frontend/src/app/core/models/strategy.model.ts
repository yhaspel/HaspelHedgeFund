export interface Universe {
  id: number;
  name: string;
  description: string;
  source: string;
  is_active: boolean;
  member_count: number;
}

export interface Portfolio {
  id: number;
  name: string;
  kind?: 'strategy' | 'manual' | 'broker';
  cash_balance: string;
  created_at: string;
}

export interface Position {
  id: number;
  ticker: string;
  quantity: string;
  avg_cost: string;
  sector: string;
  is_short: boolean;
  opened_at: string;
}

export type StrategyKind =
  | 'long_only'
  | 'short_only'
  | 'long_short'
  | 'market_neutral'
  | 'concentrated_long'
  | 'sector_rotation'
  | 'global_macro'
  | 'risk_parity'
  | 'pairs';

export const STRATEGY_KIND_OPTIONS: { value: StrategyKind; label: string }[] = [
  { value: 'long_only', label: 'Long-only' },
  { value: 'short_only', label: 'Short-only' },
  { value: 'long_short', label: 'Long/Short' },
  { value: 'market_neutral', label: 'Market-neutral' },
  { value: 'concentrated_long', label: 'Concentrated long-only' },
  { value: 'sector_rotation', label: 'Sector / thematic ETF rotation' },
  { value: 'global_macro', label: 'Global macro (ETF expression)' },
  { value: 'risk_parity', label: 'Risk-parity / multi-asset lite' },
  { value: 'pairs', label: 'Pairs trading (cointegration)' },
];

export const STRATEGY_KIND_DESCRIPTIONS: Record<StrategyKind, string> = {
  long_only:
    'Only buys stocks — never sells short. The portfolio is always pointed up: when the picks rise, the book makes money; when they fall, it loses. Simplest, cheapest setup; no borrowing fees and no short-side surprises, but no built-in cushion when the whole market drops.',
  short_only:
    'Only sells stocks short — never buys. Short-selling means borrowing a stock, selling it now, and hoping to buy it back cheaper later. The book makes money when its picks fall and loses when they rise. Pays a borrow fee on every short and can be blocked if the broker has no shares to lend.',
  long_short:
    'Buys some stocks and short-sells others at the same time. The "long" side bets on winners, the "short" side bets against losers. Because winners and losers tend to move with the market, the two sides partially cancel — the book makes money mostly from the gap between them rather than from the market direction itself. The classic hedge-fund recipe.',
  market_neutral:
    'A long/short book deliberately balanced so the dollars on each side are equal and the overall market sensitivity is close to zero. If the whole market jumps 5%, the book is designed to barely move — gains and losses come purely from whether the picked longs beat the picked shorts. Returns are smaller but much steadier; a real cushion in a crash.',
  concentrated_long:
    'A small, high-conviction long-only book: typically 5–15 stocks total, each a meaningful 5–25% of the portfolio. If fewer ideas pass the high-confidence bar than the minimum you set, the strategy refuses to add a weak pick and just holds cash. Style of an activist or "best ideas" manager — fewer bets, bigger bets, more idiosyncratic results.',
  global_macro:
    'A top-down view of the world expressed through broad-market ETFs instead of individual stocks. The macro agent classifies the current regime (growth/inflation/policy stance, etc.) and the strategy buys ETFs that fit that regime — e.g. long-duration bonds when growth slows, gold when inflation sticks, broad equity when expansion is on. To bet against a market it buys an "inverse" ETF rather than short-selling, so it never needs to borrow shares.',
  risk_parity:
    'A multi-asset basket (stocks + bonds + gold) where each sleeve is sized so that risk is roughly equal across sleeves — not dollars. Calmer assets like bonds get a bigger dollar slice, more volatile assets like tech a smaller one, so no single sleeve dominates the book\'s ups and downs. Fully mechanical and very cheap to run; rebalances only when allocations have drifted enough to be worth the trading cost.',
  pairs:
    'Picks pairs of stocks in the same industry that historically move together — like Coke and Pepsi, or Visa and Mastercard — and trades the gap between them. When one stock rallies far ahead of its partner and history says the gap usually closes again, the strategy buys the laggard and short-sells the leader, betting the two will re-converge. Because both legs are in the same sector, broad market moves cancel out: profit or loss comes almost entirely from the gap narrowing (good) or widening further (bad). Pre-set rules close each pair when the gap has reverted to normal, or force-close it if it keeps widening past a "this isn\'t reverting — get out" threshold. An optional AI-council sanity-check vets each candidate to filter out cases where the divergence has a real reason (earnings miss, lawsuit) and isn\'t just noise.',
  sector_rotation:
    'Buys a handful of sector / theme ETFs (e.g. tech, banks, energy, semiconductors, gold-miners, biotech) instead of individual stocks. Each cycle the strategy ranks ETFs by recent strength, drawdown, and how well they fit the current macro regime, then concentrates in the top few. Long-only by default; overlapping ETFs (e.g. broad tech + semiconductors) are de-duplicated so you don\'t accidentally double-bet the same theme.',
};

export interface Strategy {
  id: number;
  name: string;
  /** P7b §G: operator-set risk caveat shown on the strategy detail. */
  risk_disclaimer?: string;
  kind: StrategyKind;
  universe: number;
  universe_name: string;
  portfolio: number;
  portfolio_name: string;
  target_gross_pct: string;
  target_net_pct: string;
  max_position_pct: string;
  max_sector_pct: string;
  min_position_pct: string;
  top_k_longs: number;
  top_k_shorts: number;
  personas: string[];
  model_preset: string;
  cost_ceiling_per_cycle_usd: string;
  min_trade_notional_usd: string;
  max_turnover_pct: string;
  screener_weights: Record<string, number>;
  benchmark_ticker: string;
  beta_window_days: number;
  neutrality_tolerance_dollar_pct: string;
  neutrality_tolerance_beta: string;
  drop_on_unreliable_beta: boolean;
  max_positions: number;
  min_positions: number;
  min_aggregate_confidence: string;
  max_etfs_held: number;
  per_etf_max_pct: string;
  per_etf_min_pct: string;
  use_sector_council_v2: boolean;
  bearish_veto_threshold: string;
  asset_class_caps?: Record<string, number>;
  prefer_inverse_etf_over_short?: boolean;
  max_inverse_etf_hold_days?: number;
  vol_window_days?: number;
  rebalance_band_pct?: string;
  enable_council_veto?: boolean;
  pair_entry_z?: string;
  pair_exit_z?: string;
  pair_stop_z?: string;
  pair_max_held?: number;
  pair_notional_pct?: string;
  pair_cointegration_p_max?: string;
  pair_lookback_days?: number;
  pair_correlation_min?: string;
  enable_pair_council?: boolean;
  pair_council_min_confidence?: string;
  /**
   * P2l: gate the council fan-out behind a human review of screener picks.
   * Default true (preserves auto behavior). Set false to stop the cycle in
   * `awaiting_review` until the user approves a budget-valid subset.
   */
  auto_run_council: boolean;
  /** P4 WS-E: auto-materialize a done cycle's weights into the book. */
  auto_enroll_on_done?: boolean;
  /** P4 WS-C: count of non-cancelled cycles; gates the Delete affordance. */
  targets_count_active?: number;
  is_active: boolean;
  last_run_at: string | null;
  created_at: string;
}

/** Pre-flight cost estimate for a `Run cycle now` dispatch. `preset` echoes
 *  the resolved/chosen price tier; `overrides` is the full per-agent model map
 *  that would be used. */
export interface CycleEstimate {
  n_candidates: number;
  per_call_usd: number;
  est_total_usd: number;
  cost_ceiling_usd: number;
  exceeds_ceiling: boolean;
  per_agent: {
    agent: string;
    model: string;
    model_name: string;
    tier: string;
    per_call_usd: number;
  }[];
  overrides: Record<string, string>;
  preset: string;
}

/** Transient per-cycle model selection sent from the dispatch modal. Applies
 *  to this dispatch only — it never mutates the strategy's saved preset or the
 *  user's global model preferences. */
export interface CycleOverrideBody {
  preset?: string;
  model_overrides?: Record<string, string>;
}

export interface RebalanceOrder {
  id: number;
  ticker: string;
  side: 'buy' | 'sell' | 'short' | 'cover';
  quantity: string;
  limit_price: string | null;
  reason: string;
  estimated_notional_usd: string;
  sequence: number;
}

export interface ScreenerCandidate {
  ticker: string;
  sector: string;
  score: number;
  features: Record<string, unknown>;
  rationale: string;
}

export interface ScreenerRanking {
  id: number;
  as_of_date: string;
  long_candidates: ScreenerCandidate[];
  short_candidates: ScreenerCandidate[];
  universe_size_evaluated: number;
  created_at: string;
}

/**
 * P2l: expanded lifecycle.
 *   - queued / screening: pre-council
 *   - awaiting_review:    auto_run_council=false stops here
 *   - running_council:    candidates dispatched, transcripts being produced
 *   - constructing:       chord callback running Constructor + Rebalancer
 *   - done / failed / cancelled: terminal
 *   - running:            legacy state preserved for back-compat
 */
export type CycleStatus =
  | 'queued'
  | 'screening'
  | 'awaiting_review'
  | 'running_council'
  | 'constructing'
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled';

export const CYCLE_ACTIVE_STATUSES: CycleStatus[] = [
  'queued', 'screening', 'awaiting_review',
  'running_council', 'constructing', 'running',
];

export interface CycleSummary {
  id: number;
  as_of_date: string;
  status: CycleStatus;
  gross_pct: string;
  net_pct: string;
  realised_net_pct?: string;
  realised_portfolio_beta?: string;
  total_cost_usd: string;
  created_at: string;
  finished_at: string | null;
  /** P4 WS-B: id of the fresh cycle that superseded this terminal one. */
  superseded_by?: number | null;
  /** P4 WS-E: set when this cycle's weights were materialized into the book. */
  enrolled_at?: string | null;
}

/** P4 WS-E: one row of the enrollment preview/apply. */
export interface EnrollmentRow {
  ticker: string;
  side: 'long' | 'short';
  target_weight_pct: number;       // signed fraction
  target_notional_usd: string;
  suggested_quantity: string;      // signed
  mark_price: string | null;
  mark_source: 'mark' | 'mark_stale' | 'limit_price' | 'none';
  current_quantity: string;
  current_avg_cost: string | null;
  action: 'open' | 'increase' | 'reduce' | 'close' | 'hold' | 'skip';
  quantity_delta: string;
  rebalance_order_id: number | null;
  source_run_id: number | null;
  source_decision_id: number | null;
  warnings: string[];
}

export interface EnrollmentResult {
  target_id: number;
  as_of_date: string;
  portfolio: { id: number; name: string; kind: string; cash: string; positions_count: number };
  rows: EnrollmentRow[];
  totals: {
    gross_pct: number; net_pct: number;
    n_open: number; n_increase: number; n_reduce: number; n_close: number; n_skip: number;
  };
  enrolled: boolean;
}

export interface EnrollmentApplyRequest {
  mode: 'auto' | 'manual';
  approved_tickers?: string[];
  override_quantities?: Record<string, string>;
  note?: string;
}

export interface CandidateRunSummary {
  run_id: number;
  run_status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  tickers: string[];
  run_cost_usd: string;
  candidate_key: string;
  primary_ticker: string;
  side: 'long' | 'short' | 'sector' | 'pair';
  screener_rank: number;
  screener_score: number | null;
  sector: string;
  borrow_veto: boolean;
}

export interface CycleDetail extends CycleSummary {
  target_weights: Record<string, number>;
  beta_diagnostics?: Record<string, unknown>;
  sector_exposure: Record<string, number>;
  rejected_candidates: { ticker?: string; reason?: string; [k: string]: unknown }[];
  decisions: {
    ticker: string;
    sector: string;
    side: 'long' | 'short';
    borrow_veto: boolean;
    decision: { action: string; rationale: string; aggregate_confidence?: number };
    risk: Record<string, unknown>;
    run_id?: number;
  }[];
  screener_ranking: ScreenerRanking | null;
  orders: RebalanceOrder[];
  /** P2l: one row per candidate run linked to this target. */
  candidate_runs?: CandidateRunSummary[];
  error_message: string;
  per_position_thesis?: Record<string, {
    ticker: string;
    sector: string;
    action: string;
    aggregate_confidence: number;
    thesis: string;
    dissenting_personas?: { name: string; signal: string; confidence: number; thesis_summary?: string }[];
  }>;
  cycle_outcome?: string;
  sector_veto_log?: {
    ticker: string;
    decision: 'buy' | 'veto';
    reasons: { persona: string; signal: string; confidence: number }[];
    rm_veto: boolean;
    threshold_pct: number;
  }[];
  /** P3 addendum: cycle-level mark-to-market snapshot. */
  marked_snapshot?: CycleMarkedSnapshot;
}

export interface CycleMarkedSnapshot {
  snapshot_at: string;
  mark_as_of: string;
  since_as_of_pct: string | null;
  marked_gross_pct: string | null;
  marked_net_pct: string | null;
  per_ticker: Record<string, {
    weight_pct: string;
    as_of_price: string | null;
    as_of_price_date: string | null;
    mark_price: string | null;
    mark_price_date: string | null;
    return_pct: string | null;
    contribution_pp: string;
    warnings: string[];
  }>;
  warnings: string[];
}

export const DEFAULT_SCREENER_WEIGHTS: Record<string, number> = {
  momentum_3m: 1.0,
  momentum_6m: 0.5,
  earnings_yield: 1.0,
  quality_roic: 1.0,
  fcf_margin: 1.0,
  debt_to_equity: 0.25,
  short_drawdown: 1.0,
  short_momentum_3m: 1.0,
  short_news_neg: 1.0,
  short_debt: 0.5,
  short_quality_roic: 0.5,
};

export const SCREENER_WEIGHT_TOOLTIPS: Record<string, string> = {
  momentum_3m:
    'Bigger weight = the screener prefers names that have rallied over the last 3 months when picking long candidates.',
  momentum_6m:
    'Bigger weight = the screener prefers names that have rallied over the last 6 months for long candidates.',
  earnings_yield:
    'Cheapness signal. Earnings yield = 1 / P/E. Bigger weight = the screener prefers lower-P/E (cheaper) names for longs.',
  quality_roic:
    'Bigger weight = the screener prefers high-ROIC (better capital efficiency) names for longs.',
  fcf_margin:
    'Bigger weight = the screener prefers high free-cash-flow-margin (more profitable) names for longs.',
  debt_to_equity:
    'Penalty applied to leveraged names on the long side. Higher = more debt is treated as more disqualifying for a long.',
  short_drawdown:
    'For shorts. Bigger weight = the screener prefers names that are well off their 12-month high.',
  short_momentum_3m:
    'For shorts. Bigger weight = the screener prefers names with the worst trailing 3-month returns.',
  short_news_neg:
    'For shorts. Bigger weight = names with negative recent news get bumped up the short list.',
  short_debt:
    'For shorts. Bigger weight = leveraged names are preferred as short candidates.',
  short_quality_roic:
    'For shorts. Bigger weight = low-ROIC names are preferred as short candidates.',
};

export const SCREENER_WEIGHT_LABELS: Record<string, string> = {
  momentum_3m: '3-month momentum (long)',
  momentum_6m: '6-month momentum (long)',
  earnings_yield: 'Earnings yield (1/PE) — cheapness',
  quality_roic: 'ROIC (quality)',
  fcf_margin: 'FCF margin (quality)',
  debt_to_equity: 'Debt/equity penalty (long)',
  short_drawdown: 'Drawdown from 12-mo high (short)',
  short_momentum_3m: 'Negative 3-month momentum (short)',
  short_news_neg: 'Negative news (short)',
  short_debt: 'Leverage (short)',
  short_quality_roic: 'Low ROIC bonus (short)',
};
