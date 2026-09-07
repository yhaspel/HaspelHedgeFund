/**
 * WAVE 3 — data provenance (`GET /api/data/provenance/`).
 *
 * Where every number on a page came from and how old it is. The point of the
 * whole block is that silent staleness stops being silent: a bar series that
 * last updated three weeks ago, a regime snapshot classified off a price date
 * older than its own `as_of_date`, a provider whose key is missing.
 *
 * Verified against `backend/apps/data/views.py` (`DataProvenanceView`,
 * `_bars_provenance` … `_macro_provenance`) and `apps/runs/views.py`
 * (`ProviderDiagnosticsView`, whose `providers` / `policy` blocks are reused
 * verbatim). Two shapes are wider on the wire than the written contract:
 *   • `bars` is ALWAYS an object (with `last_date: null` when no bar exists),
 *     never `null` — typed `| null` anyway so a future narrowing is safe here.
 *   • `providers.anthropic` / `.openrouter` carry NO `freshness` key at all
 *     (they are LLM providers, not data ones), hence `freshness?`.
 */

export interface ProvenanceBars {
  last_date: string | null;
  source: string | null;
  count: number;
  /** A total-return (split/dividend adjusted) series is present. */
  adjusted_differs_from_close: boolean;
}

export interface ProvenanceDividends {
  last_ex_date: string | null;
  count: number;
}

export interface ProvenanceFilings {
  count: number;
  newest_filed_at: string | null;
}

export interface ProvenanceNewsProvider {
  provider: string;
  newest_published_at: string;
  count: number;
}

export interface ProvenanceRegime {
  as_of_date: string;
  last_price_date: string;
  stale: boolean;
  model_type: string;
}

export interface ProvenanceTicker {
  ticker: string;
  bars: ProvenanceBars | null;
  dividends: ProvenanceDividends;
  filings: ProvenanceFilings;
  news: ProvenanceNewsProvider[];
  regime: ProvenanceRegime | null;
}

export interface ProvenanceMacroSeries {
  series_id: string;
  newest_observation_date: string;
  newest_vintage_date: string;
}

export interface ProvenanceMacro {
  series: ProvenanceMacroSeries[];
  classifier_version: string | null;
  snapshot_as_of: string | null;
}

export interface ProvenanceProviderFreshness {
  last_at: string | null;
  age_days: number | null;
  count?: number;
  error?: string;
}

export interface ProvenanceProvider {
  key: 'configured' | 'missing' | string;
  user_byok: boolean;
  /** Absent for the LLM providers (anthropic / openrouter). */
  freshness?: ProvenanceProviderFreshness;
}

export interface ProvenanceGlobal {
  macro: ProvenanceMacro;
  providers: Record<string, ProvenanceProvider>;
  provider_last_success: Record<string, string | null>;
  policy: { allow_platform_data_keys?: boolean };
}

export interface ProvenanceResponse {
  as_of: string;
  tickers: Record<string, ProvenanceTicker>;
  global: ProvenanceGlobal;
}

/** `POST /api/data/provenance/refresh/` → 200. 503 carries `{detail}`. */
export interface ProvenanceRefreshResponse {
  queued: number;
  task_ids: string[];
}

/** Backend cap (`PROVENANCE_MAX_TICKERS`) — the store truncates to it. */
export const PROVENANCE_MAX_TICKERS = 50;

/** Age buckets for the freshness badge. Trading data older than a week is a bug. */
export type FreshnessLevel = 'fresh' | 'aging' | 'stale' | 'unknown';

export function freshnessOf(iso: string | null | undefined, now = Date.now()): FreshnessLevel {
  if (!iso) return 'unknown';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'unknown';
  const days = (now - t) / 86_400_000;
  if (days <= 4) return 'fresh';
  if (days <= 10) return 'aging';
  return 'stale';
}

/** "today" / "3d ago" / "5 Jun 2026" — short enough to sit in a badge. */
export function ageLabel(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const days = Math.floor((now - t) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return '1d ago';
  if (days < 45) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}
