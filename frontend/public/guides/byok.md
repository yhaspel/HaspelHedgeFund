# Bring Your Own Key (BYOK) Setup

The app does not ship with its own AI or market-data subscriptions. Instead, you connect **your own accounts** — this is called **BYOK, "Bring Your Own Key."** An *API key* is just a long password that lets one program use your account on another service. You paste your keys into the app once, and from then on the agents can think and can see market data.

This keeps you in control of your own spending and your own data, and it means the app never has to guess whose budget to charge.

## The two kinds of keys

**1. AI provider keys — so the agents can think.** The agents are powered by Large Language Models (LLMs), which run on a provider's servers. You need at least one of:

- **Anthropic** — runs the Claude models (the default high-quality choice).
- **OpenRouter** — a single account that resells many open-weight models cheaply; the best choice for low-cost runs.
- **OpenAI** — runs the GPT models.
- **Ollama** — free models running on *your own computer*. Instead of a key you provide the address of your local Ollama server. No per-use cost, but you need capable hardware.

You only need a key for the providers you actually want to use. Most people start with either Anthropic (simplest) or OpenRouter (cheapest).

**2. Market-data keys — so the agents have something to study.** The agents need stock prices, company financials, and news. These come from data providers:

- **FMP (Financial Modeling Prep)** — prices, fundamentals, and news. Required for backtests and live strategy runs. A paid service; sign up at financialmodelingprep.com.
- **Tiingo** — prices and news, used alongside or instead of FMP. A paid service; sign up at tiingo.com.
- **FRED (Federal Reserve Economic Data)** — free public economic data (interest rates, inflation, and so on), used by the macro agent. A key is optional — the app can fall back to a shared one — but your own key gives you isolation and higher rate limits.
- **EDGAR** — the U.S. regulator's free public filing archive. No key needed.

## How to set your keys

1. Open **Settings** from the sidebar and find the **Keys** section.
2. The **LLM providers** block holds your AI keys (Anthropic, OpenRouter, OpenAI, and the Ollama address).
3. The **Data providers** block holds your market-data keys (FMP, Tiingo, FRED).
4. Paste a key into its field and save. Each field is *write-only*: after saving, the app shows only whether a key is "set" or "not set" — it never displays the key back to you.

## Why some providers cost money and some do not

Public-data sources (FRED, EDGAR) are free and the app can use a shared key for them. Commercial providers (FMP, Tiingo) and all the paid AI providers are licensed per account — so the app requires *your* key and bills *your* account directly. Nothing is charged to a shared platform account on your behalf.

## Security

Keys are stored **encrypted**. They are never shown back to you after saving and never appear in any screen, log, or export. If a key leaks or you simply want to rotate it, paste a new value over the old one, or clear the field to remove it.

## What to set up first

The minimum to do anything useful: **one AI provider key** (Anthropic or OpenRouter) and **one market-data key** (FMP). Add Tiingo and a personal FRED key later if you want redundancy or higher limits.
