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
}

export interface NewsPreferencesResponse {
  preferences: NewsPreferences;
  sentiment_model_choices: SentimentModelChoice[];
  translation_model_choices: SentimentModelChoice[];
}
