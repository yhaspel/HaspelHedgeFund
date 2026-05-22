// P3 prereq 2 / WS-2 — ticker identity (name + latest market cap / P/E / EPS).
// `as_of` is *today*-data — never feed this into a backtest or PIT path.

export interface TickerProfile {
  ticker: string;
  name: string;
  exchange: string;
  sector: string;
  price: string | null;
  market_cap: string | null;
  pe_ratio: string | null;
  eps: string | null;
  shares_outstanding?: number | null;
  as_of: string;
  detail?: string;
}

export interface TickerIdentity {
  name: string;
  exchange: string;
  sector: string;
}

export interface TickerProfileBatchResponse {
  profiles: Record<string, TickerIdentity>;
}
