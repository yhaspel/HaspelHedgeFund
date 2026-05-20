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
  | 'sector_rotation';

export const STRATEGY_KIND_OPTIONS: { value: StrategyKind; label: string }[] = [
  { value: 'long_only', label: 'Long-only' },
  { value: 'short_only', label: 'Short-only' },
  { value: 'long_short', label: 'Long/Short' },
  { value: 'market_neutral', label: 'Market-neutral' },
  { value: 'concentrated_long', label: 'Concentrated long-only' },
  { value: 'sector_rotation', label: 'Sector / thematic ETF rotation' },
];

export const STRATEGY_KIND_DESCRIPTIONS: Record<StrategyKind, string> = {
  long_only:
    'Buys only. The screener surfaces long candidates and the portfolio holds positive positions; no shorting. Target net ≈ target gross. Use when you want directional upside without the borrow costs, locate risk, or short-side drawdown tail.',
  short_only:
    'Shorts only. The screener surfaces short candidates and the portfolio holds negative positions; no longs. Target net is negative (≈ −target gross). Use when you want a dedicated bearish book — pays borrow fees and is gated by locate availability.',
  long_short:
    'Both sides, directional net. The portfolio holds both long and short positions and the net (longs − shorts) is whatever you set — long-biased, short-biased, or anywhere between. Classic hedge-fund construction; gross > net, so you get some idiosyncratic exposure with reduced market beta.',
  market_neutral:
    'Both sides, dollar- AND beta-neutral. Longs and shorts are sized so that net dollars ≈ 0 and the dollar-weighted portfolio beta vs the benchmark (default SPY) ≈ 0. Returns come from the long–short spread, not from market direction. Uses a trailing 252-day OLS beta per name and a one-knob rescale to cancel the bucket betas after the standard caps.',
  concentrated_long:
    'Activist-style concentrated long-only book: 5–15 high-conviction names, no shorts, larger per-position sizes (typically 5–25% each). A candidate must clear a higher aggregate-confidence bar than a diversified book; if fewer names clear the bar than min_positions, no new target is emitted and the existing book is held (cash beats a 4th-best idea). Sector caps are loose by default because concentration is the point.',
  sector_rotation:
    'Top-down rotation across sector / thematic ETFs (SPDR sectors + themes like SOXX, ARKK, GDX, KWEB). Council fan-out is per-sector not per-name; the screener ranks ETFs by relative momentum vs SPY, drawdown, and regime-fit (dot product of the P2b macro regime vector with each ETF\'s hand-curated affinities). Long-only by default. Overlapping ETFs (e.g. XLK + SOXX) are de-duped via a holdings-overlap penalty. The universe picker should be swapped to the `sector_etfs` universe; per_etf_cap and max_etfs_held replace single-name max_position_pct.',
};

export interface Strategy {
  id: number;
  name: string;
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
  is_active: boolean;
  last_run_at: string | null;
  created_at: string;
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

export interface CycleSummary {
  id: number;
  as_of_date: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  gross_pct: string;
  net_pct: string;
  realised_net_pct?: string;
  realised_portfolio_beta?: string;
  total_cost_usd: string;
  created_at: string;
  finished_at: string | null;
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
  }[];
  screener_ranking: ScreenerRanking | null;
  orders: RebalanceOrder[];
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
