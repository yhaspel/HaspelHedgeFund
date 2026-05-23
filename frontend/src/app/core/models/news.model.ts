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
}

export interface NewsPreferences {
  sentiment_enabled: boolean;
  sentiment_model: string;
  chyron_enabled: boolean;
  chyron_item_count: number;
  feed_item_count: number;
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
}
