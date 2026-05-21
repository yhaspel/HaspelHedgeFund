"""Benchmark candidate cheap models vs Haiku baseline on a tiny slice.

Runs the council graph end-to-end over 3 tickers × 5 trading days for each
candidate model (forced via model_overrides on every agent), then reports:
  - wall-time per (ticker, day) invocation
  - structured-output failure rate (graph.invoke exceptions)
  - total cost (sum LLMCall.cost_usd) and per-call cost
  - decision agreement vs the Haiku baseline (% of personas emitting the
    same action bucket {buy, hold, sell})

Hard budget guard: aborts immediately if cumulative cost exceeds MAX_USD.

Run from backend/:
    DJANGO_SETTINGS_MODULE=hedgefund.settings.dev uv run python scripts/bench_models.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hedgefund.settings.dev")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=False)
os.environ["POSTGRES_HOST"] = "localhost"

import django  # noqa: E402

django.setup()

import logging  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)

from apps.data.models import DailyBar  # noqa: E402
from apps.data.providers.factory import get_edgar_provider, get_fmp_provider  # noqa: E402
from hedgefund_agents.graphs.council import ANALYTICAL_NODES, build_council_graph  # noqa: E402
from hedgefund_agents.models import LLMCall  # noqa: E402
from hedgefund_agents.personas import ALL_PERSONAS  # noqa: E402

UNIVERSE = ["AAPL", "MSFT", "JPM"]
N_DAYS = 5
END = dt.date(2025, 4, 1)

# Candidates to compare. Baseline first; agreement is computed against it.
CANDIDATES = [
    ("anthropic", "claude-haiku-4-5-20251001"),  # baseline
    ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
    ("openrouter", "qwen/qwen-3-32b"),
]

MAX_USD = 3.50  # hard kill-switch; well under the $4 backtest cap


def pick_days() -> list[dt.date]:
    qs = (
        DailyBar.objects.filter(ticker__in=UNIVERSE, date__lte=END)
        .values_list("date", flat=True).distinct().order_by("-date")[: N_DAYS * 2]
    )
    days = sorted(set(qs))[-N_DAYS:]
    if len(days) < N_DAYS:
        raise RuntimeError(f"Only {len(days)} trading days available; need {N_DAYS}")
    return days


def force_overrides(provider: str, model: str) -> dict[str, str]:
    spec = f"{provider}:{model}"
    names = list(ANALYTICAL_NODES.keys()) + list(ALL_PERSONAS) + [
        "risk_manager", "portfolio_manager", "macro", "news_digest",
    ]
    return dict.fromkeys(names, spec)


def action_bucket(persona_out: dict) -> str:
    # PersonaOutput.action is one of {buy, hold, sell}; defensively coerce.
    a = (persona_out or {}).get("action") or (persona_out or {}).get("recommendation")
    if isinstance(a, str):
        a = a.lower().strip()
        if a.startswith("buy") or a.startswith("long"):
            return "buy"
        if a.startswith("sell") or a.startswith("short"):
            return "sell"
        return "hold"
    return "hold"


def run_one(provider: str, model: str, days: list[dt.date]) -> dict:
    print(f"\n=== {provider}:{model} ===", flush=True)
    graph = build_council_graph(personas=list(ALL_PERSONAS))
    dp = get_fmp_provider(force_platform=True)
    fp = get_edgar_provider()
    overrides = force_overrides(provider, model)

    t0_id = LLMCall.objects.order_by("-id").values_list("id", flat=True).first() or 0
    persona_decisions: dict[tuple[str, dt.date], dict[str, str]] = {}
    n_ok = 0
    n_fail = 0
    wall_times: list[float] = []
    fail_examples: list[str] = []

    for day in days:
        for ticker in UNIVERSE:
            running_cost = sum(
                float(c.cost_usd) for c in LLMCall.objects.filter(id__gt=t0_id).only("cost_usd")
            )
            if running_cost >= MAX_USD:
                print(f"!! budget kill-switch: ${running_cost:.2f} >= ${MAX_USD}", flush=True)
                break
            state = {
                "ticker": ticker, "as_of_date": day,
                "model_overrides": overrides,
                "data_provider": dp, "filings_provider": fp,
                "use_llm_cache": False,  # benchmark must hit the network
                "disable_cio": True,
            }
            t0 = time.perf_counter()
            try:
                final = graph.invoke(state)
                wall_times.append(time.perf_counter() - t0)
                n_ok += 1
                persona_decisions[(ticker, day)] = {
                    p: action_bucket(final.get(p) or {}) for p in ALL_PERSONAS
                }
                print(f"  ok  {ticker} {day} {wall_times[-1]:5.1f}s", flush=True)
            except Exception as e:
                n_fail += 1
                fail_examples.append(f"{ticker} {day}: {type(e).__name__}: {e}"[:300])
                print(f"  FAIL {ticker} {day}: {type(e).__name__}: {e}"[:200], flush=True)
        else:
            continue
        break

    rows = list(LLMCall.objects.filter(id__gt=t0_id).values("agent_name", "cost_usd", "latency_ms"))
    total_cost = sum(float(r["cost_usd"]) for r in rows)
    per_agent_lat: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        per_agent_lat[r["agent_name"]].append(float(r["latency_ms"] or 0))

    return {
        "model": f"{provider}:{model}",
        "n_invocations_ok": n_ok,
        "n_invocations_fail": n_fail,
        "total_llm_calls": len(rows),
        "total_cost_usd": total_cost,
        "avg_wall_per_invocation_s": (sum(wall_times) / len(wall_times)) if wall_times else 0.0,
        "avg_latency_by_agent_ms": {k: sum(v) / len(v) for k, v in per_agent_lat.items()},
        "persona_decisions": persona_decisions,
        "fail_examples": fail_examples[:5],
    }


def agreement(baseline: dict, other: dict) -> float:
    bd = baseline["persona_decisions"]
    od = other["persona_decisions"]
    keys = set(bd) & set(od)
    if not keys:
        return 0.0
    total = 0
    agree = 0
    for k in keys:
        for p, ab in bd[k].items():
            if p in od[k]:
                total += 1
                if od[k][p] == ab:
                    agree += 1
    return agree / total if total else 0.0


def main() -> int:
    days = pick_days()
    print(f"[bench] universe={UNIVERSE} days={[d.isoformat() for d in days]}", flush=True)
    print(f"[bench] budget kill-switch: ${MAX_USD:.2f}", flush=True)

    results = []
    for provider, model in CANDIDATES:
        try:
            results.append(run_one(provider, model, days))
        except Exception as e:
            print(f"!! {provider}:{model} aborted: {e}", flush=True)

    baseline = results[0]
    print("\n================ SUMMARY ================")
    print(f"{'model':<45} {'ok':>4} {'fail':>5} {'$cost':>7} {'avg_s':>6} {'agree%':>7}")
    for r in results:
        ag = agreement(baseline, r) * 100 if r is not baseline else 100.0
        print(
            f"{r['model']:<45} {r['n_invocations_ok']:>4} {r['n_invocations_fail']:>5} "
            f"{r['total_cost_usd']:>7.3f} {r['avg_wall_per_invocation_s']:>6.1f} {ag:>6.1f}%"
        )
    for r in results:
        if r["fail_examples"]:
            print(f"\nFailures for {r['model']}:")
            for e in r["fail_examples"]:
                print(f"  - {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
