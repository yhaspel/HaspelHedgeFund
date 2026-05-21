import { StrategyKind } from './strategy.model';

export type InfoGuideGroup = 'getting-started' | 'strategies' | 'reference';
export type InfoGuideStatus = 'available' | 'coming-soon';

export interface InfoGuide {
  /** File name without `.md` and the `:slug` route param. */
  slug: string;
  /** Must equal the markdown's top-level `# ` line. */
  title: string;
  /** One line, shown on the library card. */
  summary: string;
  group: InfoGuideGroup;
  /** Existing symbol id in `public/icons.svg`. */
  icon: string;
  /** Extra search terms beyond title/summary. */
  keywords: string[];
  /** Optional; omitted = `'available'`. */
  status?: InfoGuideStatus;
}

export const INFO_GUIDES: InfoGuide[] = [
  // Getting Started
  {
    slug: 'welcome',
    title: 'What This Application Is',
    summary:
      'What the AI Hedge Fund does, what it does not do, and who it is for.',
    group: 'getting-started',
    icon: 'i-home',
    keywords: ['welcome', 'overview', 'introduction', 'about', 'start'],
  },
  {
    slug: 'how-to',
    title: 'How To: Runs, Backtests & Strategies',
    summary:
      'Step-by-step: run an analysis, build a strategy, and test it on history.',
    group: 'getting-started',
    icon: 'i-pulse',
    keywords: ['run', 'backtest', 'strategy', 'cycle', 'tutorial'],
  },
  {
    slug: 'byok',
    title: 'Bring Your Own Key (BYOK) Setup',
    summary:
      'Connect your own AI and market-data accounts so the app can work.',
    group: 'getting-started',
    icon: 'i-key',
    keywords: ['byok', 'api', 'key', 'anthropic', 'openrouter', 'openai', 'ollama', 'fmp', 'tiingo', 'fred'],
  },
  {
    slug: 'models-and-cost',
    title: 'Models & Cost Disclaimer',
    summary:
      'Which AI models run, what they cost, and how to keep spending under control.',
    group: 'getting-started',
    icon: 'i-alert',
    keywords: ['cost', 'price', 'token', 'preset', 'frugal', 'hybrid', 'research', 'quality', 'budget', 'disclaimer'],
  },
  // Strategies
  {
    slug: 'strategy-long-only',
    title: 'Long-Only',
    summary:
      'Buys stocks only — the simplest book, fully exposed to the market.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['long', 'long-only'],
  },
  {
    slug: 'strategy-short-only',
    title: 'Short-Only',
    summary: 'Sells stocks short only — profits when the picks fall.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['short', 'short-only', 'short selling'],
  },
  {
    slug: 'strategy-long-short',
    title: 'Long/Short',
    summary:
      'Buys winners and shorts losers — the classic hedge-fund recipe.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['long short', 'long/short', 'hedge'],
  },
  {
    slug: 'strategy-market-neutral',
    title: 'Market-Neutral',
    summary: 'A long/short book tuned so market direction barely matters.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['market neutral', 'beta neutral', 'dollar neutral'],
  },
  {
    slug: 'strategy-concentrated-long',
    title: 'Concentrated Long-Only',
    summary: 'A small number of high-conviction long bets.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['concentrated', 'high conviction', 'best ideas'],
  },
  {
    slug: 'strategy-sector-rotation',
    title: 'Sector / Thematic ETF Rotation',
    summary: 'Rotates between sector and theme ETFs by strength and regime.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['sector', 'rotation', 'etf', 'theme'],
  },
  {
    slug: 'strategy-global-macro',
    title: 'Global Macro (ETF Expression)',
    summary: 'Top-down macro views expressed through broad ETFs.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['macro', 'global macro', 'etf', 'inverse'],
  },
  {
    slug: 'strategy-risk-parity',
    title: 'Risk-Parity / Multi-Asset Lite',
    summary:
      'A stocks-plus-bonds-plus-gold book balanced by risk, not dollars.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['risk parity', 'inverse volatility', 'multi-asset', 'bonds', 'gold'],
  },
  {
    slug: 'strategy-pairs',
    title: 'Pairs Trading (Cointegration)',
    summary: 'Trades the gap between two stocks that usually move together.',
    group: 'strategies',
    icon: 'i-layers',
    keywords: ['pairs', 'cointegration', 'spread', 'z-score', 'engle granger'],
  },
  // Reference
  {
    slug: 'glossary',
    title: 'Financial Terms & Abbreviations',
    summary:
      'Plain-language definitions of every abbreviation used in the app.',
    group: 'reference',
    icon: 'i-info',
    keywords: [
      'glossary', 'terms', 'abbreviations',
      'ETF', 'HMM', 'NAV', 'PM', 'RM', 'CIO',
      'sharpe', 'sortino', 'beta', 'alpha', 'cagr', 'var',
      'dcf', 'ev/ebitda', 'ebitda', 'fcf', 'roic', 'roe',
      'sec', 'edgar', '10-k', '10-q', '8-k', 'fred', 'fmp', 'tiingo',
      'byok', 'api key', 'rate limit',
      'macro', 'regime',
    ],
  },
  {
    slug: 'agent-council',
    title: 'The Agent Council',
    summary: 'Who the AI agents are and how they reach a decision together.',
    group: 'reference',
    icon: 'i-cpu',
    keywords: ['council', 'persona', 'agent', 'risk manager', 'portfolio manager', 'cio', 'dissent', 'veto'],
  },
  {
    slug: 'market-regimes',
    title: 'Market Regimes & the Markov Classifier',
    summary: 'What a market "regime" is and how the app detects it.',
    group: 'reference',
    icon: 'i-shield',
    keywords: ['regime', 'markov', 'hmm', 'bull', 'bear', 'sideways', 'classifier'],
  },
];

/**
 * Maps a `StrategyKind` to its guide slug.
 * Convention: `strategy-${kind.replaceAll('_','-')}`.
 */
export const STRATEGY_KIND_GUIDE: Record<StrategyKind, string> = {
  long_only: 'strategy-long-only',
  short_only: 'strategy-short-only',
  long_short: 'strategy-long-short',
  market_neutral: 'strategy-market-neutral',
  concentrated_long: 'strategy-concentrated-long',
  sector_rotation: 'strategy-sector-rotation',
  global_macro: 'strategy-global-macro',
  risk_parity: 'strategy-risk-parity',
  pairs: 'strategy-pairs',
};
