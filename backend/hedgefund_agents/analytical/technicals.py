"""Technicals agent.

We compute the numbers in pure Python (SMA, RSI, ATR, momentum) — the
LLM only labels the regime and assigns a directional signal. This keeps
the LLM out of arithmetic, where it is unreliable.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import TechnicalsOutput
from ..registry import DEFAULT_MODELS, get_llm


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    diffs = np.diff(closes)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = gains[-period:].mean()
    avg_loss = losses[-period:].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _atr_pct(highs, lows, closes, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    prev_close = closes[:-1]
    tr = np.maximum.reduce(
        [
            highs[1:] - lows[1:],
            np.abs(highs[1:] - prev_close),
            np.abs(lows[1:] - prev_close),
        ]
    )
    atr = tr[-period:].mean()
    return float(atr / closes[-1] * 100)


def _momentum(closes: np.ndarray, lookback: int) -> float:
    if len(closes) <= lookback:
        return 0.0
    return float(closes[-1] / closes[-lookback - 1] - 1)


def run_technicals(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    as_of = state["as_of_date"]
    start = as_of - dt.timedelta(days=400)
    bars = state["data_provider"].get_daily_bars(ticker, start, as_of, as_of=as_of)
    if len(bars) < 30:
        # Not enough data to do anything useful — return a neutral output without LLM.
        return {"technicals": TechnicalsOutput(
            regime="range",
            momentum_1m=0.0, momentum_3m=0.0, momentum_6m=0.0,
            rsi_14=50.0, atr_pct=0.0, signal="neutral", confidence=0,
        ).model_dump()}  # type: ignore[return-value]

    closes = np.array([float(b.close) for b in bars])
    highs = np.array([float(b.high) for b in bars])
    lows = np.array([float(b.low) for b in bars])

    metrics = {
        "momentum_1m": _momentum(closes, 21),
        "momentum_3m": _momentum(closes, 63),
        "momentum_6m": _momentum(closes, 126),
        "rsi_14": _rsi(closes),
        "atr_pct": _atr_pct(highs, lows, closes),
    }
    sma50 = closes[-50:].mean() if len(closes) >= 50 else closes.mean()
    sma200 = closes[-200:].mean() if len(closes) >= 200 else closes.mean()
    last = closes[-1]

    provider, model = pick_model(state, "technicals", DEFAULT_MODELS["technicals"])
    client = get_llm(provider)
    system = (
        "You are a technical analyst. Given pre-computed indicators, label "
        "the regime and assign a directional signal. Echo the indicator "
        "values you were given — do not recompute them."
    )
    user = (
        f"Ticker: {ticker}\nAs-of: {as_of.isoformat()}\n"
        f"Last close: {last:.2f}\nSMA50: {sma50:.2f}\nSMA200: {sma200:.2f}\n"
        f"Momentum 1m/3m/6m: {metrics['momentum_1m']:.3f} / "
        f"{metrics['momentum_3m']:.3f} / {metrics['momentum_6m']:.3f}\n"
        f"RSI(14): {metrics['rsi_14']:.1f}\n"
        f"ATR%: {metrics['atr_pct']:.2f}\n"
        "Choose one regime label and a bullish/neutral/bearish signal with a "
        "0-100 confidence."
    )
    from apps.backtests.cache import make_cache_ctx
    parsed, resp = call_structured(
        client,
        model=model,
        schema=TechnicalsOutput,
        messages=[Message("system", system), Message("user", user)],
        max_tokens=4096,
        cache_ctx=make_cache_ctx(state, "technicals"),
    )
    record_llm_call(run_id=state.get("run_id"), backtest_id=state.get("backtest_id"), agent_name="technicals", resp=resp)
    # Trust our numeric metrics over whatever the LLM echoed.
    out = parsed.model_dump()
    out.update(metrics)
    return {"technicals": out}  # type: ignore[return-value]
