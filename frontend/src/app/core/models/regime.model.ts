// Markov regime classifier types (P2m). Match the backend's RegimeSnapshot
// shape returned by /api/macro/regime/* endpoints.

export type RegimeState = 'bull' | 'sideways' | 'bear';
export type RegimeModelType = 'labelled_markov' | 'gaussian_hmm';

export interface RegimeSnapshot {
  ticker: string;
  as_of_date: string;
  model_type: RegimeModelType;
  config_hash: string;
  last_price_date: string;
  current_state: RegimeState;
  current_return: number | null;
  current_state_persistence: number;
  bull_persistence: number;
  sideways_persistence: number;
  bear_persistence: number;
  bull_prob_1d: number;
  sideways_prob_1d: number;
  bear_prob_1d: number;
  bull_prob_5d: number;
  sideways_prob_5d: number;
  bear_prob_5d: number;
  bull_minus_bear_1d: number;
  prior_current_state: string;
  current_state_persistence_delta: number | null;
  bull_persistence_delta: number | null;
  bear_persistence_delta: number | null;
  state_changed_from_prior: boolean;
  stale: boolean;
}

export interface RegimeSnapshotResponse {
  ticker: string;
  as_of: string;
  snapshot: RegimeSnapshot | null;
  reason?: string;
}

export interface RegimeBatchItem {
  ticker: string;
  snapshot: RegimeSnapshot | null;
  reason?: string;
}

export interface RegimeBatchResponse {
  as_of: string;
  model_type: RegimeModelType;
  items: RegimeBatchItem[];
}

export interface RegimeHistoryItem {
  as_of_date: string;
  current_state: RegimeState;
  bull_prob_1d: number;
  sideways_prob_1d: number;
  bear_prob_1d: number;
  bull_minus_bear_1d: number;
  current_state_persistence: number;
}

export interface RegimeHistoryResponse {
  ticker: string;
  model_type: RegimeModelType;
  from: string;
  to: string;
  items: RegimeHistoryItem[];
}

export interface MarkovConsensus {
  per_ticker: Record<string, {
    state: RegimeState;
    bull_minus_bear_1d: number;
    persistence: number;
    as_of_date: string;
    stale: boolean;
  }>;
  vote: Record<RegimeState, number>;
  consensus_state: RegimeState | 'unavailable';
  consensus_strength: number;
  available_count: number;
  stale_count: number;
  missing: Array<{ ticker: string; reason: string }>;
}
