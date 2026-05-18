"""P2c smoke test: 1yr × 5 names walk-forward against live FMP + Anthropic.

Run from backend/ with:
    DJANGO_SETTINGS_MODULE=hedgefund.settings.dev uv run python scripts/smoke_walkforward.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

# Add backend/ to sys.path so "hedgefund" is importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hedgefund.settings.dev")

# Load .env from repo root for API keys.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=False)
# .env points POSTGRES_HOST=db (docker compose); for local-script use, force localhost.
os.environ["POSTGRES_HOST"] = "localhost"

import django  # noqa: E402

django.setup()

# httpx INFO logs include full request URLs; settings.LOGGING attaches a
# RedactSecretsFilter that scrubs `?token=…` / `?apikey=…` before they hit
# stdout, so we can keep INFO output for debugging.

from apps.accounts.models import User  # noqa: E402
from apps.backtests.models import Backtest  # noqa: E402
from apps.backtests.walkforward import run_walkforward  # noqa: E402

UNIVERSE = ["AAPL", "MSFT", "GOOGL", "JPM", "JNJ"]
START = dt.date(2024, 4, 1)
END = dt.date(2025, 4, 1)


def main() -> int:
    user = User.objects.get(email="owner@example.com")
    bt = Backtest.objects.create(
        user=user,
        name=f"Smoke {dt.date.today().isoformat()} 5n 1yr",
        universe=UNIVERSE,
        start_date=START,
        end_date=END,
        starting_cash=Decimal("100000"),
        commission_bps=Decimal("5"),
        spread_bps=Decimal("5"),
        rebalance_frequency="weekly",
        is_window_days=126,
        oos_window_days=42,
        step_days=42,
        n_candidates=10,
        is_objective="sharpe",
        baseline="universe_ew",
        rng_seed=42,
    )
    print(f"[smoke] created Backtest id={bt.id}", flush=True)
    t0 = time.time()
    try:
        run_walkforward(bt)
    except Exception as e:  # pragma: no cover
        print(f"[smoke] FAILED: {type(e).__name__}: {e}", flush=True)
        raise
    bt.refresh_from_db()
    elapsed = time.time() - t0
    print(
        f"[smoke] DONE id={bt.id} status={bt.status} "
        f"cost=${float(bt.total_cost_usd):.2f} elapsed={elapsed:.0f}s",
        flush=True,
    )
    if hasattr(bt, "metrics"):
        m = bt.metrics
        print(
            f"[smoke] OOS return={float(m.total_return_pct):.2f}% "
            f"sharpe(mean OOS)={float(m.mean_oos_sharpe):.2f} "
            f"deflation={float(m.sharpe_deflation):.2f} "
            f"DD={float(m.max_drawdown_pct):.2f}% "
            f"baseline={float(m.baseline_return_pct):.2f}%",
            flush=True,
        )
    return 0 if bt.status == "done" else 1


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hedgefund.settings.dev")
    sys.exit(main())
