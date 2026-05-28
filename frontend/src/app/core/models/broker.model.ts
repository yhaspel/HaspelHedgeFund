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
  description: string;
  available: boolean;
  community_unverified: boolean;
  connect_form: Array<Record<string, string>>;
}

export interface BrokerAccount {
  id: number;
  broker: string;
  broker_display: string;
  mode: BrokerMode;
  account_id: string;
  label: string;
  base_currency: string;
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
  order_type: 'market' | 'limit' | 'stop';
  limit_price: string | null;
  stop_price: string | null;
  time_in_force: 'day' | 'gtc';
  status:
    | 'draft'
    | 'confirmed'
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
  submitted_at: string | null;
  filled_at: string | null;
  cancelled_at: string | null;
  avg_fill_price: string | null;
  filled_quantity: string;
  error_message: string;
  group_id: string | null;
  notional_estimate: string;
  created_at: string;
}

export interface CreateOrderRequest {
  broker_account: number;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: string | number;
  order_type?: 'market' | 'limit' | 'stop';
  limit_price?: string | number | null;
  stop_price?: string | number | null;
  time_in_force?: 'day' | 'gtc';
  decision?: number | null;
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
