// P3-prereq-4 — market-news page types.

export type Sentiment = 'bullish' | 'bearish' | 'neutral';

export interface MarketNewsItem {
  id: number;
  provider: string;
  headline: string;
  summary: string;
  url: string;
  image_url: string;
  source: string;
  published_at: string;
  symbols: string[];
  tags: string[];
  cluster_size: number;
  language: string | null;
  translated_from: string | null;
  original_headline?: string | null;
  original_summary?: string | null;
  sentiment: Sentiment | null;
  sentiment_score: number | null;
  sentiment_rationale: string | null;
  sentiment_model: string | null;
}

/**
 * WAVE 3 — why the news LLM features did (or did not) run.
 *
 * Sentiment + translation became BYOK-required with a daily USD cap: with no
 * user OpenRouter key (and `platform_key_allowed === false`) the feature is
 * skipped, and the model pickers are limited to the frugal preset menu.
 * `reason` is the backend's own user-facing sentence — render it verbatim.
 * Verified against `apps/data/news_llm_policy.py::llm_status`.
 */
export interface NewsLlmStatus {
  byok_required: boolean;
  has_user_key: boolean;
  platform_key_allowed: boolean;
  allowed: boolean;
  reason: string | null;
  daily_cap_usd: number;
  spent_today_usd: number;
  /** True ⇒ non-frugal models are `selectable: false` and PUT rejects them. */
  model_choices_restricted: boolean;
}

export interface NewsFeed {
  items: MarketNewsItem[];
  page: number;
  page_size: number;
  total_available: number;
  has_more: boolean;
  generated_at: string;
  sentiment_enabled: boolean;
  sentiment_model: string | null;
  providers_used: string[];
  warnings: string[];
  needs_keys: boolean;
  chyron_enabled: boolean;
  chyron_item_count: number;
  ranking_basis: string;
  sentiment_warning?: string;
  translation_warning?: string;
  /** WAVE 3 — BYOK / daily-cap state. */
  llm_status?: NewsLlmStatus;
  /** P4-OFF — rows are last-persisted DB values, not a live fetch. */
  stale?: boolean;
}

export interface NewsPreferences {
  sentiment_enabled: boolean;
  sentiment_model: string;
  chyron_enabled: boolean;
  chyron_item_count: number;
  feed_item_count: number;
  translation_enabled: boolean;
  translation_model: string;
  translation_fallback_model: string;
}

export interface SentimentModelChoice {
  id: string;
  display_name: string;
  price_in_per_mtok: number | null;
  price_out_per_mtok: number | null;
  /** Dedicated reasoning model — rendered with a 🧠 prefix. */
  supports_reasoning: boolean;
  /** In the cheap Llama/Qwen default subset; shown before "Show all models". */
  frugal: boolean;
  /**
   * WAVE 3 — false when this model may NOT be picked (a non-frugal model and
   * the user has no OpenRouter key of their own). `PUT /news/preferences/`
   * answers 400 `{detail}` for one of these, so the picker disables it.
   *
   * Optional so a pre-wave-3 server (which omits the key) does not read as
   * "everything is locked" — call sites test `selectable === false`, never
   * falsiness.
   */
  selectable?: boolean;
}

export interface NewsPreferencesResponse {
  preferences: NewsPreferences;
  sentiment_model_choices: SentimentModelChoice[];
  translation_model_choices: SentimentModelChoice[];
  /** WAVE 3 — BYOK / daily-cap state (same block as the feed). */
  llm_status?: NewsLlmStatus;
}
