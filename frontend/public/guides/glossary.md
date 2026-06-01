# Financial Terms & Abbreviations

Plain-language definitions of every abbreviation and piece of jargon used across the app. Skim it once, then come back when a term puzzles you. Grouped by topic; within each group, terms run roughly from most to least common.

## The application

### AI Hedge Fund

This application: a tool for researching investment strategies with a team of AI agents, against simulated (paper) money.

### Run / Analysis

A one-off study of a chosen set of stocks by the AI council, producing a recommendation. The quickest way to use the app.

### Strategy

A standing, automated portfolio with a fixed set of rules. It runs on its own and decides what to hold. Each *strategy type* behaves differently; every type has its own guide.

### Cycle

One iteration of a strategy: the council reviews the universe, proposes trades, and the book is updated. Strategies run cycle after cycle.

### Backtest

A replay of a strategy against historical data, to see how it *would* have performed. A test of an idea, not a promise about the future.

### Universe

The list of stocks or ETFs a strategy or run is allowed to choose from. Picking the universe is picking the playing field.

### Portfolio / Book

The current holdings plus cash. "Book" is industry slang for the same thing.

### Paper trading

Trading with simulated money. Everything in this app is paper trading: positions and profits are simulated. You can connect a *paper* brokerage account (Alpaca paper, or the built-in Demo broker) to place simulated orders; live, real-money trading is disabled.

### Ticker / Symbol

The short code that identifies a security on an exchange, like `AAPL` (Apple) or `SPY` (an S&P 500 ETF).

### Broker / Brokerage account

The connected account through which the app places orders. Today only *paper* (simulated) accounts are supported — **Alpaca** paper and a built-in **Demo** broker. See the *Connect a Broker Account* guide.

### Leaderboard

A scoreboard that ranks agents, models, and strategies on how well their past calls actually turned out. See the *Leaderboards* guide.

### Scheduled run

An analysis that runs automatically on a recurring schedule over a watchlist, notifying you only when something material changes. See the *Schedules & Automation* guide.

## The AI layer

### LLM (Large Language Model)

The kind of AI that powers every agent. It reads text and writes text; here, it reads market data and writes investment opinions.

### Agent

One AI worker with a single defined job (for example, "value this company" or "manage overall risk").

### Council

The full group of agents that study and debate the stocks together before a decision is made.

### Persona

An agent modelled on a famous investor (Warren Buffett, Cathie Wood, and others), carrying that investor's philosophy and style.

### Analytical agent

An agent that computes objective inputs (fundamentals, valuation, technicals, sentiment) for the personas to use. Less opinion, more measurement.

### Risk Manager (RM)

The agent that watches overall risk and can veto positions that breach hard limits.

### Portfolio Manager (PM)

The agent that makes the final call, weighing every other agent's view into concrete trades.

### CIO (Chief Investment Officer)

A senior oversight agent that sets high-level direction for the council.

### Signal

An agent's verdict on a stock: **bullish** (expects it to rise), **bearish** (expects it to fall), or **neutral**.

### Confidence

How sure an agent is of its signal, scored 0 to 100.

### Thesis

The written reasoning behind an agent's signal.

### Dissent / Dissenting view

A minority opinion that disagrees with the final decision. The app always records dissent rather than hiding it.

### Veto

A hard override. The Risk Manager can veto a trade that breaks a firm rule, regardless of how much the council likes it.

### Council alpha

How much value the AI council adds *beyond* a simple mechanical baseline. If it is near zero, the council is not earning its cost.

### Model preset

A named bundle of model choices (`frugal`, `hybrid`, `research`, `quality`, `dev`) that sets the price/quality trade-off in one click.

### Token

The unit of text an LLM processes and is billed by — roughly three-quarters of a word. **Prompt tokens** are sent in; **completion tokens** come out.

### Context window

The maximum amount of text (in tokens) a model can consider at once.

### Prompt caching

Reusing the unchanged part of a prompt across calls to cut cost and latency.

### Structured output

A machine-readable answer (a fixed data form) rather than free text, so the app can act on it reliably.

### HMM (Hidden Markov Model)

A statistical model that infers a *hidden* state of the world (such as "the market is in a bull regime") from observable data (such as returns and volatility). The "hidden" part means the state is never observed directly, only estimated. Used by the regime classifier.

### Markov chain / Markov process

A model in which the next state depends only on the *current* state, not on the full history. The mathematical backbone of regime detection.

## Hedge funds & portfolios

### Hedge fund

A pooled investment vehicle that uses flexible techniques — including short-selling and hedging — to seek returns in any market direction.

### Long / Long position

Owning a security. A long position profits when the price rises.

### Short / Short position / Short-selling

Borrowing a security, selling it now, and aiming to buy it back cheaper later. A short position profits when the price *falls*. It carries extra costs and risks (see borrow fee, locate).

### Borrow fee

The rental cost of borrowing shares in order to short them.

### Locate

A broker's confirmation that shares are actually available to borrow. No locate, no short.

### ETF (Exchange-Traded Fund)

A single tradable security that holds a basket of assets. Buying one ETF gives you exposure to its whole basket — an entire index, sector, or theme — in one trade.

### Inverse ETF

An ETF engineered to go *up* when its target index goes *down*. It lets a strategy bet against a market without short-selling.

### Sector ETF

An ETF tracking one slice of the economy (technology, energy, financials, …). The eleven "SPDR" sector ETFs are a common standard set.

### NAV (Net Asset Value)

The total value of a portfolio: holdings plus cash. Position sizes are usually quoted as a percentage of NAV.

### Gross exposure

The size of all positions added together, long and short, as a percentage of NAV. A measure of how *active* the book is.

### Net exposure

Long positions minus short positions. A measure of which way the book *leans*. Near zero means balanced; high positive means it mostly bets on prices rising.

### Leverage

Taking on market exposure greater than your capital. Amplifies both gains and losses.

### Dollar-neutral

Holding equal dollar amounts long and short, so the two sides offset.

### Beta-neutral / Market-neutral

Built so the book's overall sensitivity to the market is close to zero (see beta). Returns then come from stock selection, not market direction.

### Position sizing

Deciding how much capital to put into each holding.

### Concentration limit / Maximum position

A cap on how large any single holding may be, so one bet cannot dominate the book.

### Sleeve

A sub-portfolio within a larger book. A risk-parity book, for example, is built from several sleeves.

### Rebalance

Adjusting holdings back toward their target weights after prices have moved them off target.

### Rebalance band

How far a holding may drift from its target before a rebalance is triggered. A wider band means less trading.

### Turnover

How much of the portfolio is bought and sold over a period. High turnover means high trading costs.

### Notional

The dollar size of a position or trade. **Minimum trade notional** is the smallest trade worth making once costs are considered.

### Slippage

The gap between the price you expected and the price you actually got.

### Order ticket

The final, concrete trade instruction: which ticker, buy or sell, how much.

### Market order

An order to trade immediately at the best price currently available. Fast, but the exact fill price is not guaranteed.

### Limit order

An order to trade only at a stated price or better. The price is controlled, but the order may not fill if the market never reaches it.

### Whole shares vs. fractional shares

Whole-share orders trade in units of one share; fractional orders allow a fraction of a share, so a fixed-dollar target can be matched exactly. Some brokers support fractional shares; others do not.

### Mark / Mark price

The current price used to value a holding. A portfolio's positions are "marked" to recent prices to compute their value and profit. A mark can be a live quote, a delayed quote, or the last daily close.

## Performance & risk metrics

### Alpha

Return earned from skill — the part not explained by simply riding the market.

### Beta

How strongly a holding or portfolio moves with the overall market. Beta of 1 moves with the market; 0 is independent; below 0 moves opposite.

### Sharpe ratio

Return earned per unit of risk taken. Higher is better; it rewards *steady* returns over jumpy ones. The headline "is this good?" number.

### Sortino ratio

Like the Sharpe ratio, but it only counts *downside* volatility as risk, on the view that upside swings are not a problem.

### Drawdown / Maximum drawdown

The drop from a peak to the following trough. Maximum drawdown is the worst such drop in the period — a gut-check on "how bad did it get."

### Volatility

How much returns fluctuate. High volatility means a bumpy ride. Usually measured as standard deviation.

### Standard deviation

A statistic for how spread out a set of numbers is around their average. Applied to returns, it is the standard measure of volatility.

### CAGR (Compound Annual Growth Rate)

The smooth yearly growth rate that would produce the actual total return over the period. A way to state "X% per year."

### Annualised

A figure rescaled to a one-year basis so periods of different length can be compared fairly.

### VaR (Value at Risk)

An estimate of the most you would expect to lose over a given period, at a given confidence level (e.g. "95% of days, losses stay under $X").

### Equity curve

A chart of the portfolio's value over time. The first thing to look at in a backtest.

### Correlation

How closely two return streams move together: +1 in lockstep, 0 unrelated, −1 exactly opposite. Two strategies with high correlation are not really diversifying you.

### Hit rate

The share of an agent's directional (bullish or bearish) calls that turned out correct. The headline accuracy number on the agents leaderboard.

### Brier score

A measure of how well-*calibrated* a confidence score is: it compares stated confidence against actual outcomes. Lower is better — a well-calibrated agent that says "70% confident" is right about 70% of the time.

## Valuation & fundamentals

### Fundamentals

The facts of a company's financial health: revenue, profit, debt, cash flow, and so on.

### Valuation

Estimating what a company is actually worth, to compare against its market price.

### DCF (Discounted Cash Flow)

A valuation method that projects a company's future cash and translates it into a value today (future money is worth less than money now).

### Fair value / Fair-value band

An estimate of what a stock *should* be worth. A band rather than a point, because the estimate is uncertain.

### P/E (Price-to-Earnings ratio)

Share price divided by earnings per share. A rough gauge of how expensive a stock is relative to its profits.

### PEG (Price/Earnings-to-Growth ratio)

The P/E ratio divided by the company's growth rate, to judge whether a high P/E is justified by fast growth.

### EV/EBITDA (Enterprise Value to EBITDA)

A valuation ratio that, unlike P/E, accounts for a company's debt. Common when comparing companies with different debt loads.

### EBITDA (Earnings Before Interest, Taxes, Depreciation and Amortisation)

A measure of operating profit that strips out financing and accounting effects to show the core business result.

### FCF (Free Cash Flow)

The cash a company has left after running and maintaining itself. The cash genuinely available to investors.

### Owner earnings

Warren Buffett's preferred profit measure: the cash an owner could take out without harming the business.

### ROIC (Return on Invested Capital)

How much profit a company generates per dollar of capital put to work. A core test of business quality.

### ROE (Return on Equity)

Profit generated per dollar of shareholders' equity.

### Book value

A company's net worth on its balance sheet: assets minus liabilities.

### Net-net

A deep-value test from Benjamin Graham: a stock trading below the value of its current assets minus *all* its liabilities. Very rare.

### Margin of safety

Buying well below your estimate of fair value, so you are protected if the estimate is wrong. A central value-investing idea.

### Moat

A durable competitive advantage that protects a company's profits from rivals (a strong brand, a network effect, high switching costs).

### TAM (Total Addressable Market)

The total revenue available if a product captured its entire potential market. A growth-investing yardstick.

### Residual income

A valuation lens based on profit earned *above* the cost of the capital employed.

### Multiples

Valuing a company by comparison ratios (like P/E or EV/EBITDA) against its peers, rather than from cash-flow projections.

## Strategy mechanics & statistics

### Momentum

The tendency of recent winners to keep winning and recent losers to keep losing, at least for a while. Momentum strategies buy strength.

### Mean-reversion

The tendency of prices, or the gap between two prices, to drift back toward an average after straying far from it. Mean-reversion strategies bet on the snap-back.

### Cointegration

A statistical relationship where two prices wander individually but their *spread* stays tethered together over the long run. The foundation of pairs trading.

### Engle-Granger test

The specific statistical test the app uses to check whether two stocks are cointegrated.

### Spread

The price difference between two related securities. Pairs trading is the business of trading the spread.

### Z-score

How unusual a value is, measured in standard deviations from its average. A z-score of +2 means "two standard deviations above normal — quite stretched." Used to time pairs trades.

### Hedge ratio

How many units of one stock to trade against one unit of another so the two legs are properly balanced.

### p-value

A statistical confidence figure. A small p-value (say, below 0.05) means a result is unlikely to be a fluke. Used to accept or reject candidate pairs.

### Inverse-volatility weighting

Sizing each holding in inverse proportion to its volatility: calmer assets get more dollars, jumpier assets get fewer, so each contributes similar risk.

### Risk parity

A portfolio approach that equalises the *risk* contributed by each sleeve, rather than equalising the dollars.

### Statistical arbitrage (stat-arb)

A family of strategies that profit from small, statistically identified price relationships, holding many such bets at once. Pairs trading is the classic example.

### Screener

An automated first-pass filter that scans the universe and ranks or selects candidates by rules, before any AI debate.

### Stop / Stop level

A pre-set exit point that closes a position to cap a loss or to bail out when a trade thesis has clearly failed.

### Confidence threshold

The minimum agent confidence required for an idea to make it into the portfolio. Raising it makes the book more selective.

### In-sample vs. out-of-sample

In-sample data is what a strategy was designed and tuned on; out-of-sample is fresh data it has never seen. Honest testing happens out-of-sample.

### Overfitting

Tuning a strategy so tightly to past data that it captures noise instead of a real pattern, and then fails on new data.

### Look-ahead bias

Accidentally letting a backtest use information that would not have been known yet at that point in time. It produces fake-good results. The app's point-in-time engine is designed to prevent it.

### Survivorship bias

Testing only on companies that still exist today, silently ignoring those that failed — which flatters the results.

### Point-in-time data

Data reconstructed as it actually stood on a given past date, with no later revisions. Essential for an honest backtest.

### Walk-forward

A backtesting method that steps forward through time, always testing on data that comes *after* the data used to decide — mimicking how a strategy would really be run.

## Market data, providers & filings

### SEC (Securities and Exchange Commission)

The U.S. regulator that requires public companies to file standardised financial reports.

### EDGAR

The SEC's free public database of those filings.

### 10-K

A company's comprehensive **annual** report to the SEC.

### 10-Q

A company's **quarterly** report to the SEC; lighter than a 10-K.

### 8-K

A filing that reports a **material event** between regular reports (a CEO change, an acquisition, a major loss).

### Earnings transcript

The written record of a company's earnings call with analysts. A source of management tone and forward guidance.

### FRED (Federal Reserve Economic Data)

A free public database of economic statistics (interest rates, inflation, employment) published by the St. Louis Fed. Feeds the macro agent.

### FMP (Financial Modeling Prep)

A commercial provider of prices, company fundamentals, and news used by the app.

### Tiingo

A commercial provider of prices and news used by the app.

### BYOK (Bring Your Own Key)

The model where you connect your *own* provider accounts via API keys, so usage is billed to you. See the BYOK Setup guide.

### API key

A long secret string that lets the app use one of your provider accounts on your behalf. Treat it like a password.

### Rate limit

A cap a provider sets on how many requests you may make in a given time. Your own key generally gives you a higher limit than a shared one.

## Macro & market regimes

### Macro / Macroeconomics

The big-picture forces that move all markets at once: growth, inflation, interest rates, central-bank policy, currencies.

### Macro regime

A description of the current macro environment, often as a quadrant of growth and inflation (e.g. "growth slowing, inflation high").

### Regime (market)

The prevailing market mood, classified by the app as **bull** (rising, calm), **sideways** (range-bound), or **bear** (falling, volatile).

### Yield curve

A chart of interest rates across borrowing lengths (short-term to long-term). Its shape is a closely watched signal of where the economy is headed.

### Duration

A bond's sensitivity to interest-rate changes. Long-duration bonds swing more when rates move.

### Quadrant

The growth-by-inflation grid (growth up/down × inflation up/down) the macro agent uses to summarise the regime.

### Risk-on / Risk-off

Shorthand for market mood. In *risk-on* periods investors favour riskier assets like stocks; in *risk-off* periods they retreat to safer ones like bonds and gold.
