export interface GlossaryTerm {
  /** Stable key used in markup, e.g. 'sharpe'. */
  id: string;
  /** Display label, e.g. 'Sharpe ratio'. */
  term: string;
  /** One-sentence definition shown in the tooltip. */
  short: string;
  /** Heading slug in glossary.md, e.g. 'sharpe-ratio'. */
  anchor: string;
}

/**
 * Curated subset of glossary terms intended for `hf-term` inline popovers.
 * Each `anchor` must correspond to a real `##`/`###` heading slug in
 * `glossary.md` — enforced by a drift-guard test.
 */
export const GLOSSARY_TERMS: Record<string, GlossaryTerm> = {
  // The application
  run: {
    id: 'run',
    term: 'Run',
    short: 'A one-off council analysis of a chosen set of stocks, producing a recommendation.',
    anchor: 'run-analysis',
  },
  strategy: {
    id: 'strategy',
    term: 'Strategy',
    short: 'A standing, automated portfolio with fixed rules. Each strategy type behaves differently.',
    anchor: 'strategy',
  },
  cycle: {
    id: 'cycle',
    term: 'Cycle',
    short: 'One iteration of a strategy: the council reviews the universe, proposes trades, the book is updated.',
    anchor: 'cycle',
  },
  backtest: {
    id: 'backtest',
    term: 'Backtest',
    short: 'A replay of a strategy against historical data — a test of an idea, not a promise about the future.',
    anchor: 'backtest',
  },
  universe: {
    id: 'universe',
    term: 'Universe',
    short: 'The list of stocks or ETFs a strategy is allowed to choose from.',
    anchor: 'universe',
  },
  ticker: {
    id: 'ticker',
    term: 'Ticker',
    short: 'The short code that identifies a security on an exchange, like AAPL.',
    anchor: 'ticker-symbol',
  },
  // The AI layer
  llm: {
    id: 'llm',
    term: 'LLM',
    short: 'Large Language Model — the kind of AI that powers every agent.',
    anchor: 'llm-large-language-model',
  },
  council: {
    id: 'council',
    term: 'Council',
    short: 'The full group of agents that study and debate the stocks before a decision is made.',
    anchor: 'council',
  },
  persona: {
    id: 'persona',
    term: 'Persona',
    short: 'An agent modelled on a famous investor, carrying that investor\'s philosophy.',
    anchor: 'persona',
  },
  rm: {
    id: 'rm',
    term: 'Risk Manager',
    short: 'The agent that watches overall risk and can veto positions that breach hard limits.',
    anchor: 'risk-manager-rm',
  },
  pm: {
    id: 'pm',
    term: 'Portfolio Manager',
    short: 'The agent that makes the final call — weighing every other agent\'s view into concrete trades.',
    anchor: 'portfolio-manager-pm',
  },
  signal: {
    id: 'signal',
    term: 'Signal',
    short: 'An agent\'s verdict: bullish, bearish, or neutral.',
    anchor: 'signal',
  },
  confidence: {
    id: 'confidence',
    term: 'Confidence',
    short: 'How sure an agent is of its signal, scored 0 to 100.',
    anchor: 'confidence',
  },
  preset: {
    id: 'preset',
    term: 'Model preset',
    short: 'A named bundle of model choices (frugal, hybrid, research, quality, dev) setting the price/quality trade-off.',
    anchor: 'model-preset',
  },
  token: {
    id: 'token',
    term: 'Token',
    short: 'The unit of text an LLM is billed by — roughly three-quarters of a word.',
    anchor: 'token',
  },
  hmm: {
    id: 'hmm',
    term: 'HMM',
    short: 'Hidden Markov Model — infers a hidden state (e.g. market regime) from observable data.',
    anchor: 'hmm-hidden-markov-model',
  },
  // Portfolios
  long: {
    id: 'long',
    term: 'Long position',
    short: 'Owning a security — profits when the price rises.',
    anchor: 'long-long-position',
  },
  short: {
    id: 'short',
    term: 'Short position',
    short: 'Borrowing and selling a security, aiming to buy it back cheaper later — profits when the price falls.',
    anchor: 'short-short-position-short-selling',
  },
  etf: {
    id: 'etf',
    term: 'ETF',
    short: 'Exchange-Traded Fund — one tradable security holding a basket of assets.',
    anchor: 'etf-exchange-traded-fund',
  },
  nav: {
    id: 'nav',
    term: 'NAV',
    short: 'Net Asset Value — the total value of a portfolio (holdings plus cash).',
    anchor: 'nav-net-asset-value',
  },
  'gross-exposure': {
    id: 'gross-exposure',
    term: 'Gross exposure',
    short: 'The size of all positions added together (long + short), as a percentage of NAV.',
    anchor: 'gross-exposure',
  },
  'net-exposure': {
    id: 'net-exposure',
    term: 'Net exposure',
    short: 'Long positions minus short positions — which way the book leans.',
    anchor: 'net-exposure',
  },
  'dollar-neutral': {
    id: 'dollar-neutral',
    term: 'Dollar-neutral',
    short: 'Holding equal dollar amounts long and short, so the two sides offset.',
    anchor: 'dollar-neutral',
  },
  'beta-neutral': {
    id: 'beta-neutral',
    term: 'Beta-neutral',
    short: 'Built so the book\'s overall sensitivity to the market (beta) is close to zero.',
    anchor: 'beta-neutral-market-neutral',
  },
  turnover: {
    id: 'turnover',
    term: 'Turnover',
    short: 'How much of the portfolio is bought and sold over a period — high turnover means high trading costs.',
    anchor: 'turnover',
  },
  // Risk & performance
  alpha: {
    id: 'alpha',
    term: 'Alpha',
    short: 'Return earned from skill — the part not explained by simply riding the market.',
    anchor: 'alpha',
  },
  beta: {
    id: 'beta',
    term: 'Beta',
    short: 'How strongly a holding moves with the overall market. 1 moves with it; 0 is independent.',
    anchor: 'beta',
  },
  sharpe: {
    id: 'sharpe',
    term: 'Sharpe ratio',
    short: 'Return per unit of risk taken — the headline "is this good?" number. Higher is better.',
    anchor: 'sharpe-ratio',
  },
  sortino: {
    id: 'sortino',
    term: 'Sortino ratio',
    short: 'Like Sharpe, but it only counts downside volatility as risk.',
    anchor: 'sortino-ratio',
  },
  drawdown: {
    id: 'drawdown',
    term: 'Drawdown',
    short: 'The drop from a peak to the following trough. Maximum drawdown is the worst such drop.',
    anchor: 'drawdown-maximum-drawdown',
  },
  cagr: {
    id: 'cagr',
    term: 'CAGR',
    short: 'Compound Annual Growth Rate — the smooth yearly growth rate equivalent to the actual total return.',
    anchor: 'cagr-compound-annual-growth-rate',
  },
  var: {
    id: 'var',
    term: 'VaR',
    short: 'Value at Risk — the most you would expect to lose over a period at a given confidence level.',
    anchor: 'var-value-at-risk',
  },
  // Strategy mechanics
  momentum: {
    id: 'momentum',
    term: 'Momentum',
    short: 'The tendency of recent winners to keep winning. Momentum strategies buy strength.',
    anchor: 'momentum',
  },
  'mean-reversion': {
    id: 'mean-reversion',
    term: 'Mean-reversion',
    short: 'The tendency of prices to drift back toward an average after straying. Pairs trading is the classic case.',
    anchor: 'mean-reversion',
  },
  cointegration: {
    id: 'cointegration',
    term: 'Cointegration',
    short: 'Two prices wander individually but their spread stays tethered together — the foundation of pairs trading.',
    anchor: 'cointegration',
  },
  zscore: {
    id: 'zscore',
    term: 'Z-score',
    short: 'How unusual a value is, measured in standard deviations from its average. +2 means quite stretched.',
    anchor: 'z-score',
  },
  spread: {
    id: 'spread',
    term: 'Spread',
    short: 'The price difference between two related securities. Pairs trading is the business of trading the spread.',
    anchor: 'spread',
  },
  overfitting: {
    id: 'overfitting',
    term: 'Overfitting',
    short: 'Tuning a strategy so tightly to past data that it captures noise instead of a real pattern.',
    anchor: 'overfitting',
  },
  'look-ahead': {
    id: 'look-ahead',
    term: 'Look-ahead bias',
    short: 'Accidentally letting a backtest use information that wouldn\'t have been known yet — produces fake-good results.',
    anchor: 'look-ahead-bias',
  },
  // Data providers
  edgar: {
    id: 'edgar',
    term: 'EDGAR',
    short: 'The SEC\'s free public database of company filings (10-K, 10-Q, 8-K).',
    anchor: 'edgar',
  },
  fmp: {
    id: 'fmp',
    term: 'FMP',
    short: 'Financial Modeling Prep — a commercial provider of prices, fundamentals, and news.',
    anchor: 'fmp-financial-modeling-prep',
  },
  tiingo: {
    id: 'tiingo',
    term: 'Tiingo',
    short: 'A commercial provider of prices and news used by the app.',
    anchor: 'tiingo',
  },
  fred: {
    id: 'fred',
    term: 'FRED',
    short: 'Federal Reserve Economic Data — free public economic statistics used by the macro agent.',
    anchor: 'fred-federal-reserve-economic-data',
  },
  byok: {
    id: 'byok',
    term: 'BYOK',
    short: 'Bring Your Own Key — you connect your own provider accounts via API keys, so usage is billed to you.',
    anchor: 'byok-bring-your-own-key',
  },
  // Regimes
  regime: {
    id: 'regime',
    term: 'Regime',
    short: 'The prevailing market mood — bull (rising, calm), sideways (range-bound), or bear (falling, volatile).',
    anchor: 'regime-market',
  },
};
