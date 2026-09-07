/**
 * P3a-1 broker types. Generated server-side shapes that the broker store
 * and pages consume. Decimals come back as strings from DRF so they're
 * typed accordingly.
 */

export type BrokerAuthKind =
  | 'none'
  | 'api_key'
  | 'oauth2'
  | 'oauth1'
  | 'gateway_session';

export type BrokerMode = 'paper' | 'live';

export type BrokerConnectionStatus =
  | 'connecting'
  | 'active'
  | 'needs_reauth'
  | 'disabled'
  | 'error';

export interface BrokerCapability {
  code: string;
  display_name: string;
  auth_kind: BrokerAuthKind;
  supports_paper: boolean;
  supports_live: boolean;
  supports_fractional: boolean;
  quantity_increment: string;
  supported_order_types: string[];
  supported_time_in_force: string[];
  supports_bracket: boolean;
  description: string;
  /**
   * Whether the ADAPTER exists at all — still `true` for IBKR/TradeStation.
   * Do NOT gate the connect UI on this: use `enabled` below.
   */
  available: boolean;
  community_unverified: boolean;
  connect_form: Array<Record<string, string>>;
  /**
   * WAVE 3 — the DEPLOYMENT gate (`ENABLED_BROKERS` + the deferred list).
   * `false` ⇒ `POST /api/broker-accounts/` answers 400 `{broker: "<note>"}`
   * (a plain string, not a list) and no new account may be created. Existing
   * accounts are untouched, so a config change never orphans a connected book.
   */
  enabled?: boolean;
  status?: 'enabled' | 'deferred' | 'disabled' | 'unavailable';
  /** The human reason a non-enabled broker cannot be connected. "" when enabled. */
  note?: string;
}

export type BrokerOrderType =
  | 'market'
  | 'limit'
  | 'stop'
  | 'stop_limit'
  | 'trailing_stop';

export type BrokerOrderClass = 'simple' | 'bracket' | 'oto' | 'oco';

export type BrokerLegRole = '' | 'entry' | 'stop_loss' | 'take_profit';

export type BrokerGroupStatus =
  | 'draft'
  | 'confirmed'
  | 'working'
  | 'closed'
  | 'cancelled'
  | 'error';

export interface BrokerAccount {
  id: number;
  broker: string;
  broker_display: string;
  mode: BrokerMode;
  account_id: string;
  label: string;
  base_currency: string;
  default_quantity_mode: 'whole' | 'fractional';
  connection_status: BrokerConnectionStatus;
  is_active: boolean;
  last_synced_at: string | null;
  created_at: string;
  portfolio_id: number;
  portfolio_name: string;
  credential: { status: 'set' | 'unset' };
  drift_pending: boolean;
}

export interface BrokerPortfolioSnapshot {
  cash_balance: string;
  positions: Array<{
    ticker: string;
    quantity: string;
    avg_cost: string;
    is_short: boolean;
    realized_pnl: string;
  }>;
}

export interface BrokerSideSnapshot {
  cash: string;
  buying_power: string;
  equity: string;
  currency: string;
}

export interface BrokerFill {
  id: number;
  order: number;
  broker_fill_id: string;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: string;
  price: string;
  filled_at: string;
  created_at: string;
}

export interface BrokerDrift {
  pending: boolean;
  last_event_id: number | null;
  last_notes: string;
}

export interface BrokerAccountOverview {
  account: BrokerAccount;
  broker: BrokerSideSnapshot;
  portfolio: BrokerPortfolioSnapshot;
  recent_fills: BrokerFill[];
  drift: BrokerDrift;
}

export interface BrokerOrderRow {
  id: number;
  broker_account: number;
  client_order_id: string;
  decision_id: number | null;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: string;
  order_type: BrokerOrderType;
  limit_price: string | null;
  stop_price: string | null;
  trail_price: string | null;
  trail_percent: string | null;
  time_in_force: 'day' | 'gtc';
  status:
    | 'draft'
    | 'confirmed'
    /** Accepted and owned by the backend, but deliberately NOT sent to the
     *  broker yet — it is released at (or shortly after) the next open. These
     *  rows are live commitments the user can still cancel. */
    | 'pending_open'
    | 'submitted'
    | 'partial'
    | 'filled'
    | 'cancelled'
    | 'rejected'
    | 'error';
  idempotency_state: 'unsubmitted' | 'submit_pending' | 'acknowledged' | 'unknown';
  broker_order_id: string;
  confirmed_at: string | null;
  confirmation_method: '' | 'manual_ui' | 'scheduled_job' | 'api';
  queued_until_open: boolean;
  /** True while the order is held locally instead of being sent to the broker. */
  is_held: boolean;
  /** When the hold is expected to release (ISO-8601 UTC), null when unknown. */
  release_eta: string | null;
  /** The earliest instant the release job may submit it (ISO-8601 UTC). */
  release_after: string | null;
  submitted_at: string | null;
  filled_at: string | null;
  cancelled_at: string | null;
  avg_fill_price: string | null;
  filled_quantity: string;
  error_message: string;
  group_id: string | null;
  parent_order: number | null;
  leg_role: BrokerLegRole;
  // Present only on a group anchor (the bracket/OTO entry or the OCO primary).
  legs: BrokerOrderRow[];
  group_status: BrokerGroupStatus | null;
  notional_estimate: string;
  created_at: string;
  // Returned by the confirm endpoint for a bracket/OTO entry (not on list rows).
  max_loss?: string | null;
  target_gain?: string | null;
}

export interface CreateOrderRequest {
  broker_account: number;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: string | number;
  quantity_mode?: 'whole' | 'fractional';
  order_type?: BrokerOrderType;
  limit_price?: string | number | null;
  stop_price?: string | number | null;
  time_in_force?: 'day' | 'gtc';
  decision?: number | null;
  // Bracket / OTO / OCO carrier fields. order_class !== 'simple' creates a
  // grouped order; the anchor is returned with nested `legs`.
  order_class?: BrokerOrderClass;
  take_profit_limit_price?: string | number | null;
  stop_loss_stop_price?: string | number | null;
  stop_loss_limit_price?: string | number | null;
  trail_price?: string | number | null;
  trail_percent?: string | number | null;
}

export interface ConfirmOrderRequest {
  typed_confirmation?: string;
  live_confirmation?: string;
  confirmation_method?: 'manual_ui' | 'scheduled_job' | 'api';
}

export interface CalendarSummary {
  is_open: boolean;
  now_eastern: string;
  next_open: string;
  next_close: string;
}

export interface LiveDisclaimer {
  version: string;
  body: string;
  effective_from: string;
  is_current: boolean;
  accepted: boolean;
}

// --- P3a-2: IBKR gateway connect endpoints ---------------------------------

export interface IBKRRuntimeConfig {
  gateway_login_url: string;
}

export interface IBKRGatewayProbeResult {
  reachable: boolean;
  payload?: unknown;
  detail?: string;
}

export interface IBKRGatewayAuthStatus {
  authenticated: boolean;
  connected: boolean;
  competing: boolean;
  ready: boolean;
  raw?: Record<string, unknown>;
  detail?: string;
}

export interface IBKRDiscoveredAccount {
  account_id: string;
  is_paper: boolean;
}

export interface IBKRGatewayDiscoverResult {
  accounts: IBKRDiscoveredAccount[];
  selected: string | null;
}

// --- P3a-3: TradeStation OAuth + connect endpoints -------------------------

export interface TradeStationRuntimeConfig {
  configured: boolean;
  source: 'user' | 'env' | '';
  redirect_uri: string;
}

export interface TradeStationAppCredentials {
  has_user_credentials: boolean;
  client_id_masked: string;
  source: 'user' | 'env' | '';
}

export interface TradeStationOAuthStartResult {
  authorization_url: string;
  state: string;
}

export interface TradeStationDiscoveredAccount {
  account_id: string;
  type: string;
  currency: string;
  status: string;
}

export interface TradeStationDiscoverResult {
  accounts: TradeStationDiscoveredAccount[];
}
