export type AssetClass = 'equity' | 'etf' | 'all';

export type FieldGroup =
  | 'Descriptive'
  | 'Liquidity & Volume'
  | 'Performance'
  | 'Fundamental';

export type FieldKind = 'range' | 'enum' | 'bool';

export interface ScreenerField {
  id: string;
  label: string;
  group: FieldGroup;
  kind: FieldKind;
  unit: string;
  asset_classes: string[];
  enum_values: string[];
  description: string;
  available: boolean;
  requires: string[];
}

export interface ScreenerFieldsResponse {
  fields: ScreenerField[];
  capabilities: string[];
}

export interface Preset {
  id: string;
  name: string;
  description: string;
  asset_class: AssetClass;
  filters: Record<string, ScreenCriterion>;
  sort: { field: string; dir: 'asc' | 'desc' };
  limit: number;
  available: boolean;
  requires: string[];
}

export interface PresetsResponse {
  presets: Preset[];
}

export type ScreenCriterion =
  | { min?: number; max?: number }
  | { values: string[] }
  | { value: boolean };

export interface ScreenRequest {
  asset_class: AssetClass;
  criteria: Record<string, ScreenCriterion>;
  sort: { field: string; dir: 'asc' | 'desc' };
  limit?: number;
  preset_id?: string;
}

export interface ScreenResultRow {
  ticker: string;
  name: string;
  exchange: string;
  sector: string;
  industry: string;
  is_etf: boolean;
  price: string | null;
  change_pct: number | null;
  gap_pct: number | null;
  rvol: number | null;
  volume: number | null;
  adv_14d: number | null;
  dollar_volume: number | null;
  market_cap: string | null;
  pe_ratio: string | null;
  eps: string | null;
  beta: string | null;
  momentum_1m: number | null;
  momentum_3m: number | null;
  momentum_6m: number | null;
  dist_52w_high: number | null;
  dist_52w_low: number | null;
  above_50d_ma: boolean | null;
  above_200d_ma: boolean | null;
  has_positive_catalyst: boolean;
  in_watchlist: boolean;
  warnings: string[];
  /**
   * WAVE 3 — how this row's bar-derived metrics were sourced.
   *   `cache`   — served from stored bars (no provider call).
   *   `fetched` — bars were pulled for this run.
   *   `partial` — the enrichment could not complete: momentum, 52-week
   *               distances and MA flags are NULL on this row and a row-level
   *               `warnings` entry says why. Treating those nulls as zero is
   *               how a screener quietly ranks on missing data.
   */
  enrichment?: 'cache' | 'fetched' | 'partial';
}

export interface ScreenResult {
  rows: ScreenResultRow[];
  universe_size: number;
  enriched_count: number;
  returned_count: number;
  truncated: boolean;
  as_of: string;
  provider: string;
  capabilities: string[];
  warnings: string[];
  preset_id?: string;
  /** WAVE 3 — how the run's rows were sourced. Sums to `rows.length`. */
  enrichment_counts?: { cache: number; fetched: number; partial: number };
}

export interface SavedScreen {
  id: number;
  name: string;
  asset_class: AssetClass;
  filters: Record<string, ScreenCriterion>;
  sort: { field: string; dir: 'asc' | 'desc' };
  based_on: string;
  created_at: string;
  updated_at: string;
}

export interface WatchlistItem {
  id: number;
  ticker: string;
  note: string;
  added_at: string;
  price?: string | null;
  change_pct?: number | null;
  rvol?: number | null;
  volume?: number | null;
  market_cap?: string | null;
}

export interface WatchlistResponse {
  items: WatchlistItem[];
  name: string;
}
