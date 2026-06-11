/**
 * P3: Manual Book — types mirroring `apps/portfolios/serializers.py`.
 *
 * All decimal quantities are exchanged as strings to avoid IEEE-754
 * rounding artefacts; the UI parses them through `Number(...)` only at
 * the final display step.
 */

export type PortfolioKind = 'strategy' | 'manual';

export type PositionSide = 'long' | 'short';

export type QuantityMode = 'whole' | 'fractional';

export type MarkCadence = 'daily' | 'delayed' | 'manual';

export interface PortfolioPreferences {
  mark_cadence: MarkCadence;
  interval_minutes: number;
  last_refreshed_at: string | null;
}

export interface PositionValuation {
  id: number;
  ticker: string;
  quantity: string;
  avg_cost: string;
  is_short: boolean;
  sector: string;
  opened_at: string;
  opened_via: 'manual' | 'run' | 'strategy_cycle';
  source_run_id: number | null;
  source_decision_id: number | null;
  note: string;
  realized_pnl: string;
  mark_price: string | null;
  mark_as_of: string | null;
  mark_stale: boolean;
  market_value: string;
  unrealized_pnl: string;
  unrealized_pnl_pct: string | null;
  weight_pct: string;
  warnings: string[];
}

export interface PortfolioOverview {
  portfolio_id: number;
  name: string;
  kind: PortfolioKind;
  cash_balance: string;
  reserved_short_proceeds: string;
  free_cash: string;
  total_value: string;
  long_market_value: string;
  short_market_value: string;
  gross_exposure_pct: string;
  net_exposure_pct: string;
  unrealized_pnl: string;
  realized_pnl: string;
  positions: PositionValuation[];
  preferences: PortfolioPreferences | null;
  warnings: string[];
}

export interface LedgerEntry {
  id: number;
  kind:
    | 'deposit'
    | 'withdrawal'
    | 'position_open'
    | 'position_increase'
    | 'position_reduce'
    | 'position_close'
    | 'edit_adjustment';
  ticker: string;
  quantity_delta: string;
  price: string | null;
  cash_delta: string;
  realized_pnl: string;
  quantity_after: string | null;
  cash_balance_after: string;
  position: number | null;
  source_run: number | null;
  source_decision: number | null;
  note: string;
  created_at: string;
}

export interface SuggestionFactor {
  key: 'run_decision' | 'strategy' | 'macro' | 'cash_cap' | 'position_cap';
  label: string;
  effect: string;
  detail: string;
}

export interface PositionSuggestion {
  ticker: string;
  side: PositionSide;
  suggested_weight_pct: string;
  target_notional_usd: string;
  target_quantity: string;
  suggested_notional_usd: string;
  suggested_quantity: string;
  quantity_mode: QuantityMode;
  rounding_residual_usd: string;
  current_price: string;
  price_as_of: string | null;
  portfolio_total_value: string;
  free_cash: string;
  existing_quantity: string;
  existing_side: 'long' | 'short' | 'flat';
  action_label: 'Open position' | 'Increase position' | 'Reduce/close first';
  factors: SuggestionFactor[];
  warnings: string[];
}

export interface OpenPositionRequest {
  ticker: string;
  side: PositionSide;
  quantity: string;
  entry_price: string;
  quantity_mode?: QuantityMode;
  source_run?: number | null;
  source_decision?: number | null;
  note?: string;
}

export interface ClosePositionRequest {
  exit_price?: string;
  quantity?: string;
  quantity_mode?: QuantityMode;
  note?: string;
}

export interface EditPositionRequest {
  quantity?: string;
  avg_cost?: string;
  note?: string;
  quantity_mode?: QuantityMode;
}

export interface CashAdjustRequest {
  kind: 'deposit' | 'withdrawal';
  amount: string;
  note?: string;
}

export interface MutationResponse {
  portfolio: PortfolioOverview;
  ledger_entry: LedgerEntry;
}

/** One row in the Portfolios hub — a single book the user owns. */
export interface PortfolioHubBook {
  kind: 'manual' | 'broker' | 'strategy';
  portfolio_id: number;
  name: string;
  subtitle: string;
  cash: string;
  market_value: string;
  equity: string;
  /** P10 §C5: true = marked to market; false = cost-basis fallback. */
  marked: boolean;
  positions_count: number;
  /** Frontend route to that book's proper surface. */
  link_route: string;
  /** Broker connection status, when kind === 'broker'. */
  status: string;
}

export interface PortfolioHub {
  books: PortfolioHubBook[];
  totals: {
    books: number;
    /** P10 §C5: real capital only (broker + manual), marked to market. */
    cash: string;
    equity: string;
    /** Strategy paper-mirror subtotal, reported separately. */
    mirror_books: number;
    mirror_equity: string;
  };
}
