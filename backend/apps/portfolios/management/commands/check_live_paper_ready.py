"""P02e review: live-paper readiness check.

Validates that a strategy is ready to be promoted from research → live
paper trading. Reports pass/fail per check; exit code is non-zero when
any required check fails.

Usage:
    uv run python manage.py check_live_paper_ready --strategy-id 1
    uv run python manage.py check_live_paper_ready --strategy-id 1 --json

What it checks:
    1. Provider keys: required LLM provider key is present (user BYOK or
       platform fallback).
    2. Universe feature completeness: every active universe member has
       at least one recent DailyBar row.
    3. Borrow coverage: every short-side candidate has a BorrowQuote OR
       the strategy is long-only.
    4. Dry-cycle budget: estimated cost fits inside
       ``cost_ceiling_per_cycle_usd``.
    5. Caps: ``max_position_pct``, ``max_sector_pct`` are non-zero and
       sane (max_position_pct < 1, max_sector_pct < 1).
    6. Kill switch: the strategy can be paused via ``is_active=False``
       without touching the data path. P10 §D1 gave ``is_active`` ARCHIVE
       semantics: archived strategies are hidden from the default
       strategies list and skipped by the nightly leaderboard recompute.
       Autopilot dispatch still keys on ``StrategyAutopilot.is_enabled``
       (disable the autopilot to stop trading; archive to declutter).

This command never sends an order, never touches a broker, and never
makes an LLM call. It is a pre-flight gate for the operator.
"""
from __future__ import annotations

import datetime as dt
import json as _json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max

from apps.data.models import DailyBar
from apps.portfolios.models import (
    BorrowQuote,
    PortfolioStrategy,
    UniverseMembership,
)


class Check:
    __slots__ = ("name", "passed", "detail", "required")

    def __init__(self, name: str, passed: bool, detail: str, *, required: bool = True):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.required = required

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "required": self.required,
        }


def check_provider_keys(strategy: PortfolioStrategy) -> list[Check]:
    """At least one LLM provider key must be reachable."""
    from django.conf import settings

    user = strategy.user
    try:
        from apps.models_catalog.models import ProviderKey

        pk = ProviderKey.objects.filter(user=user).first()
    except Exception:
        pk = None

    def _avail(name: str, setting: str) -> bool:
        user_has = bool(pk and pk.has_key(name))
        env_has = bool(getattr(settings, setting, ""))
        return user_has or env_has

    has_anthropic = _avail("anthropic", "ANTHROPIC_API_KEY")
    has_openrouter = _avail("openrouter", "OPENROUTER_API_KEY")
    return [
        Check(
            "llm_provider_key",
            has_anthropic or has_openrouter,
            (
                f"anthropic={'set' if has_anthropic else 'unset'}, "
                f"openrouter={'set' if has_openrouter else 'unset'}"
            ),
        ),
        Check(
            "fmp_key",
            _avail("fmp", "FMP_API_KEY"),
            "FMP required for market data and fundamentals.",
        ),
    ]


def check_universe_features(strategy: PortfolioStrategy) -> Check:
    """Every active universe member must have at least one DailyBar row
    that's no more than 7 days old."""
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=7)
    members = list(
        UniverseMembership.objects.filter(
            universe=strategy.universe,
            effective_from__lte=today,
        ).values_list("ticker", flat=True)
    )
    if not members:
        return Check(
            "universe_features",
            False,
            f"universe {strategy.universe.name!r} has no members.",
        )
    fresh_ts = (
        DailyBar.objects.filter(ticker__in=members, date__gte=cutoff)
        .values("ticker")
        .annotate(last=Max("date"))
    )
    fresh_tickers = {r["ticker"] for r in fresh_ts}
    missing = [t for t in members if t not in fresh_tickers]
    return Check(
        "universe_features",
        len(missing) == 0,
        (
            f"{len(members) - len(missing)}/{len(members)} members have "
            f"daily bars within 7 days. Missing: {missing[:10]}"
            + ("…" if len(missing) > 10 else "")
        ),
    )


def check_borrow_coverage(strategy: PortfolioStrategy) -> Check:
    """For any strategy that can short, at least one current short
    candidate must have a BorrowQuote. Long-only strategies pass this
    check trivially."""
    kind = strategy.kind
    can_short = kind in {
        PortfolioStrategy.KIND_LONG_SHORT,
        PortfolioStrategy.KIND_SHORT_ONLY,
        PortfolioStrategy.KIND_MARKET_NEUTRAL,
        PortfolioStrategy.KIND_PAIRS,
    }
    if not can_short:
        return Check(
            "borrow_coverage", True,
            f"{kind} is long-only — no borrow needed.",
            required=False,
        )
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=14)
    universe_tickers = list(
        UniverseMembership.objects.filter(
            universe=strategy.universe,
            effective_from__lte=today,
        ).values_list("ticker", flat=True)
    )
    if not universe_tickers:
        return Check("borrow_coverage", False, "universe is empty.")
    quoted = set(
        BorrowQuote.objects.filter(
            ticker__in=universe_tickers, as_of_date__gte=cutoff
        ).values_list("ticker", flat=True)
    )
    coverage = len(quoted) / max(1, len(universe_tickers))
    passed = coverage >= 0.50  # at least half the names quoted within 14 days
    return Check(
        "borrow_coverage",
        passed,
        f"{len(quoted)}/{len(universe_tickers)} ({coverage:.0%}) quoted in last 14 days.",
    )


def check_caps_sane(strategy: PortfolioStrategy) -> Check:
    issues: list[str] = []
    if strategy.max_position_pct <= 0 or strategy.max_position_pct >= Decimal("1"):
        issues.append(f"max_position_pct={strategy.max_position_pct}")
    if strategy.max_sector_pct <= 0 or strategy.max_sector_pct >= Decimal("1"):
        issues.append(f"max_sector_pct={strategy.max_sector_pct}")
    if strategy.target_gross_pct <= 0:
        issues.append(f"target_gross_pct={strategy.target_gross_pct}")
    if strategy.cost_ceiling_per_cycle_usd <= 0:
        issues.append(f"cost_ceiling_per_cycle_usd={strategy.cost_ceiling_per_cycle_usd}")
    return Check(
        "caps_sane",
        not issues,
        "ok" if not issues else "; ".join(issues),
    )


def check_budget(strategy: PortfolioStrategy) -> Check:
    """Estimate the per-cycle cost (uses the same path as the API) and
    compare to ``cost_ceiling_per_cycle_usd``."""
    from apps.portfolios.tasks import estimate_cycle

    try:
        est = estimate_cycle(strategy)
    except Exception as e:
        return Check("budget", False, f"estimate failed: {e}")
    return Check(
        "budget",
        not est["exceeds_ceiling"],
        (
            f"est ${est['est_total_usd']:.2f} vs cap "
            f"${est['cost_ceiling_usd']:.2f} ({est['n_candidates']} candidates)."
        ),
    )


def check_kill_switch(strategy: PortfolioStrategy) -> Check:
    """``is_active=False`` = archived (P10 §D1: hidden from the default list,
    skipped by leaderboard recompute). Sanity-check the field exists and
    reflects the current run-state. To stop TRADING, disable the autopilot."""
    return Check(
        "kill_switch",
        hasattr(strategy, "is_active"),
        f"is_active={strategy.is_active}; archive (False) hides + skips it; "
        "disable the autopilot to stop trading.",
        required=False,
    )


def run_all_checks(strategy: PortfolioStrategy) -> list[Check]:
    out: list[Check] = []
    out.extend(check_provider_keys(strategy))
    out.append(check_universe_features(strategy))
    out.append(check_borrow_coverage(strategy))
    out.append(check_caps_sane(strategy))
    out.append(check_budget(strategy))
    out.append(check_kill_switch(strategy))
    return out


class Command(BaseCommand):
    help = "Validate a strategy is ready for live-paper trading promotion."

    def add_arguments(self, parser):
        parser.add_argument("--strategy-id", type=int, required=True)
        parser.add_argument("--json", action="store_true", help="Emit JSON report.")

    def handle(self, *args, **opts):
        try:
            strategy = PortfolioStrategy.objects.get(pk=opts["strategy_id"])
        except PortfolioStrategy.DoesNotExist as e:
            raise CommandError(
                f"strategy id={opts['strategy_id']} does not exist"
            ) from e
        results = run_all_checks(strategy)
        passed = all(r.passed for r in results if r.required)
        if opts["json"]:
            self.stdout.write(_json.dumps({
                "strategy_id": strategy.pk,
                "strategy_name": strategy.name,
                "kind": strategy.kind,
                "ready": passed,
                "checks": [r.to_dict() for r in results],
            }, indent=2))
        else:
            self.stdout.write(
                f"Strategy {strategy.pk} — {strategy.name!r} ({strategy.kind})"
            )
            for r in results:
                mark = "✓" if r.passed else ("⚠" if not r.required else "✗")
                self.stdout.write(f"  {mark} {r.name}: {r.detail}")
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS("READY") if passed
                else self.style.ERROR("NOT READY")
            )
        if not passed:
            raise CommandError("strategy is not live-paper ready.")
