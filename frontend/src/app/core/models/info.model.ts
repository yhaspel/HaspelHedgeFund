import { StrategyKind } from './strategy.model';

export type InfoGuideGroup =
  | 'getting-started'
  | 'strategies'
  | 'trading-automation'
  | 'reference';
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
    slug: 'local-model-setup',
    title: 'Local Model Setup with Ollama',
    summary:
      'Run AI models on your own computer for free — install Ollama, pull a model, and select it for an agent.',
    group: 'getting-started',
    icon: 'i-cpu',
    keywords: [
      'ollama', 'local', 'model', 'free', 'self-host', 'on-prem',
      'pull', 'install', 'brew', 'serve', 'daemon',
      'qwen', 'llama', 'mistral', 'phi',
      'gpu', 'metal', 'apple silicon', 'cpu', 'ram',
      'hybrid', 'dev', 'cost', 'budget',
    ],
  },
  {
    slug: 'tradestation-setup',
    title: 'TradeStation Setup',
    summary:
      'Register a TradeStation developer app, ready for when paper/live connection ships.',
    group: 'getting-started',
    icon: 'i-key',
    keywords: [
      'tradestation', 'broker', 'oauth', 'paper', 'sim', 'simulated',
      'live', 'client id', 'client secret', 'redirect uri', 'developer app',
    ],
    status: 'coming-soon',
  },
  {
    slug: 'telegram-setup',
    title: 'Telegram Notifications Setup',
    summary:
      'Create a Telegram bot and get a chat id so scheduled-run alerts can reach you on Telegram.',
    group: 'getting-started',
    icon: 'i-bell',
    keywords: [
      'telegram', 'bot', 'botfather', 'chat id', 'notifications', 'alerts',
      'schedule', 'scheduled run', 'token', 'channel', 'webhook',
    ],
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
  // Trading & Automation
  {
    slug: 'connect-broker',
    title: 'Connect a Broker Account',
    summary:
      'Connect a paper broker (Alpaca) or the built-in Demo broker so the app can place orders.',
    group: 'trading-automation',
    icon: 'i-link',
    keywords: [
      'broker', 'brokerage', 'alpaca', 'paper', 'demo', 'connect', 'account',
      'credentials', 'api key', 'secret', 'disconnect', 'reconnect', 'ibkr',
      'tradestation', 'live',
    ],
  },
  {
    slug: 'placing-orders',
    title: 'Placing Orders from a Run',
    summary:
      'Turn a run\'s decisions into broker orders: ticket, whole vs fractional, confirm gates, fills.',
    group: 'trading-automation',
    icon: 'i-arrow-rt',
    keywords: [
      'order', 'broker order', 'buy', 'sell', 'market', 'limit', 'whole share',
      'fractional', 'quantity', 'submit', 'confirm', 'notional', 'ticket',
      'fill', 'cancel', 'add to portfolio',
    ],
  },
  {
    slug: 'portfolios',
    title: 'Portfolios & Books',
    summary:
      'Manual, strategy, and broker books: positions, cash, NAV, exposure, marks, and the ledger.',
    group: 'trading-automation',
    icon: 'i-wallet',
    keywords: [
      'portfolio', 'book', 'position', 'holding', 'cash', 'nav', 'exposure',
      'gross', 'net', 'ledger', 'mark', 'marks', 'enroll', 'enrol',
      'manual book', 'strategy book', 'broker book', 'weight', 'realized',
      'unrealized',
    ],
  },
  {
    slug: 'schedules',
    title: 'Schedules & Automation',
    summary:
      'Run a watchlist through the council on a recurring schedule, with cost ceilings and alerts.',
    group: 'trading-automation',
    icon: 'i-rerun',
    keywords: [
      'schedule', 'scheduled run', 'automation', 'cron', 'cadence', 'daily',
      'weekly', 'weekday', 'watchlist', 'materiality', 'notification', 'alert',
      'cost ceiling', 'auto-submit', 'market-aware', 'pause', 'resume',
    ],
  },
  {
    slug: 'leaderboard',
    title: 'Leaderboards',
    summary:
      'How agents, models, and strategies are ranked on real outcomes — hit rate, cost, council alpha.',
    group: 'trading-automation',
    icon: 'i-pulse',
    keywords: [
      'leaderboard', 'ranking', 'performance', 'hit rate', 'brier', 'sharpe',
      'sortino', 'drawdown', 'council alpha', 'contrarian', 'provisional',
      'agents', 'models', 'strategies', 'scorecard',
    ],
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
  {
    slug: 'search',
    title: 'App-Wide Search',
    summary:
      'The Cmd/Ctrl-K command palette for runs, strategies, and backtests, plus transcript search.',
    group: 'reference',
    icon: 'i-search',
    keywords: [
      'search', 'command palette', 'cmd k', 'ctrl k', 'find', 'jump',
      'runs', 'strategies', 'backtests', 'tickers', 'transcript', 'filter',
    ],
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
