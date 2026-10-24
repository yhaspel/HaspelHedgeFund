"""Verify the autonomous fund's weekly cron cycle ran cleanly (P10 §A1/§A3).

Read-only. The pods are the ENABLED autopilots, read from StrategyAutopilot
(not hard-coded), together with the account(s) they trade and their
universes. Checks that every pod fired on a given date, did NOT skip on the
stale-data guard, that the risk-parity pod generated the expected
residual-liquidation orders, and that the pods' universe data is fresh +
adjusted-close-clean. Optionally pushes a pass/fail summary to the owner's
notification channels.

    python manage.py verify_friday_cycle                      # defaults to the most recent Friday
    python manage.py verify_friday_cycle --date 2026-06-12
    python manage.py verify_friday_cycle --date 2026-06-12 --notify

Exit code is non-zero if any check fails (so a wrapper/launchd job can detect it).
"""
from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

RESIDUAL_TICKERS = ["MRVL", "SIRI", "ZETA"]
MAX_BAR_STALENESS_DAYS = 6
FACTOR_TOL = 0.005


class Command(BaseCommand):
    help = "Verify the autonomous fund's weekly cron cycle ran cleanly."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--date", default=None,
            help="Cycle fire date YYYY-MM-DD (UTC). Default: most recent Friday.",
        )
        parser.add_argument(
            "--notify", action="store_true",
            help="Push the summary to the owner's notification channels.",
        )
        parser.add_argument(
            "--owner-email",
            default=getattr(settings, "ALPACA_FUND_OWNER_EMAIL", ""),
            help="Notification target; default: settings.ALPACA_FUND_OWNER_EMAIL.",
        )

    def handle(self, *args, **opts) -> None:
        from apps.data.models import DailyBar
        from apps.portfolios import autopilot_risk
        from apps.portfolios.models import (
            AutopilotRun,
            PortfolioStrategy,
            Position,
            RebalanceOrder,
            StrategyAutopilot,
        )
        from apps.portfolios.tasks import _active_members

        if opts["date"]:
            cycle_date = dt.date.fromisoformat(opts["date"])
        else:
            today = timezone.now().date()
            cycle_date = today - dt.timedelta(days=(today.weekday() - 4) % 7)  # most recent Friday

        lines: list[str] = []
        results: list[tuple[str, bool]] = []

        def check(name: str, ok: bool, detail: str = "") -> None:
            results.append((name, ok))
            mark = "PASS" if ok else "FAIL"
            lines.append(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))

        # The live layout: one pod per ENABLED autopilot (a disabled or archived
        # pod is not expected to fire), plus the account(s) it trades.
        autopilots = list(
            StrategyAutopilot.objects.filter(is_enabled=True)
            .select_related("strategy", "broker_account")
            .order_by("strategy_id")
        )
        if not autopilots:
            check("Enabled autopilot pods", False, "no StrategyAutopilot is enabled")

        # 1 + 2 + 5a — autopilot runs per pod on cycle_date.
        runs_by_strat: dict[int, AutopilotRun | None] = {}
        any_stale = False
        for ap in autopilots:
            sid = ap.strategy_id
            run = (
                AutopilotRun.objects.filter(
                    autopilot__strategy_id=sid, fire_time_utc__date=cycle_date
                )
                .order_by("-fire_time_utc")
                .first()
            )
            runs_by_strat[sid] = run
            name = ap.strategy.name
            if run is None:
                check(f"#{sid} {name}: cycle fired", False, f"no AutopilotRun on {cycle_date}")
                continue
            ok = run.status not in (AutopilotRun.SKIPPED, AutopilotRun.HALTED, AutopilotRun.FAILED)
            last = run.autopilot.last_run_at
            check(f"#{sid} {name}: cycle fired", ok, f"status={run.status}, last_run_at={last}")
            if (run.submit_decision or {}).get("skipped") == "stale_data":
                any_stale = True

        check(
            "No pod skipped on stale/corrupt data (StaleMarketDataError guard)",
            not any_stale,
            "one or more pods skipped with reason=stale_data" if any_stale else "clean",
        )

        # 3 — RP pod generated Wave-0 close orders for the residuals still held
        # in its book of record (its fund sleeve), the only book it trades.
        rp = next(
            (a for a in autopilots if a.strategy.kind == PortfolioStrategy.KIND_RISK_PARITY), None,
        )
        rp_run = runs_by_strat.get(rp.strategy_id) if rp is not None else None
        if rp is None:
            lines.append("[INFO] no enabled risk-parity pod; residual check skipped")
        elif rp_run is not None and rp_run.target_id:
            close_orders = list(
                RebalanceOrder.objects.filter(
                    target_id=rp_run.target_id, reason="close",
                    ticker__in=RESIDUAL_TICKERS,
                ).values_list("ticker", "side", "quantity")
            )
            got = {t for t, _, _ in close_orders}
            book = autopilot_risk.member_book(rp)
            book_id = book.pk if book is not None else None
            still_held = [
                t for t in RESIDUAL_TICKERS
                if Position.objects.filter(portfolio_id=book_id, ticker=t).exists()
            ]
            ok = bool(got) or not still_held  # generated closes, or nothing left to close
            check(
                "RP residuals (MRVL/SIRI/ZETA): liquidation orders generated",
                ok,
                f"close orders {sorted(got)}; still held {still_held} "
                "(fills release Mon market open)",
            )
        else:
            check("RP residuals: liquidation orders generated", False, "no RP target on cycle_date")

        # 4 — the pods' universe data freshness + adjusted-close integrity, over
        # the members the cycle actually trades on cycle_date.
        tickers: set[str] = {
            t for a in autopilots for t, _sector in _active_members(a.strategy, cycle_date)
        }
        stale, corrupt = [], []
        for t in sorted(tickers):
            b = DailyBar.objects.filter(ticker=t, source="fmp").order_by("-date").first()
            if b is None:
                stale.append(f"{t}:none")
                continue
            if (cycle_date - b.date).days > MAX_BAR_STALENESS_DAYS:
                stale.append(f"{t}:{b.date}")
            c = float(b.close)
            if c > 0 and abs(float(b.adjusted_close) / c - 1.0) > FACTOR_TOL:
                corrupt.append(f"{t}:{float(b.adjusted_close)/c:.4f}")
        clean_note = "all current, factor~1.0"
        detail = (f"stale={stale} " if stale else "") + (
            f"corrupt={corrupt}" if corrupt else clean_note
        )
        check(
            f"Universe data fresh + adjusted-close clean ({len(tickers)} ETFs)",
            not stale and not corrupt,
            detail,
        )

        # 5b — stray open orders on each pod account (best-effort; Alpaca-side).
        accounts = {a.broker_account_id: a.broker_account for a in autopilots if a.broker_account}
        for acc in accounts.values():
            try:
                from apps.brokers.reconcile import get_broker

                cli = get_broker(acc)._client  # type: ignore[attr-defined]
                from alpaca.trading.enums import QueryOrderStatus
                from alpaca.trading.requests import GetOrdersRequest

                opens = cli.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
                strays = [f"{o.symbol} {o.side} {o.qty} {o.order_type}" for o in opens]
                order_note = f"open Alpaca orders on acct {acc.pk}: {strays or 'none'}"
            except Exception as exc:  # noqa: BLE001
                order_note = f"Alpaca order check skipped for acct {acc.pk}: {exc}"
            lines.append(f"[INFO] {order_note}")

        n_pass = sum(1 for _, ok in results if ok)
        n_total = len(results)
        all_ok = n_pass == n_total
        header = f"Friday-cycle verification ({cycle_date}): {n_pass}/{n_total} checks passed" + (
            " — ALL GREEN" if all_ok else " — ATTENTION NEEDED"
        )
        report = header + "\n" + "\n".join(lines)
        self.stdout.write(report)

        if opts["notify"]:
            self._notify(opts["owner_email"], header, report)

        if not all_ok:
            raise SystemExit(1)

    def _notify(self, owner_email: str, subject: str, body: str) -> None:
        from django.contrib.auth import get_user_model

        from apps.notifications.models import NotificationChannel
        from apps.notifications.services import send_notification

        u = get_user_model().objects.filter(email=owner_email).first()
        if u is None:
            self.stderr.write(f"notify: no user {owner_email}")
            return
        for ch in NotificationChannel.objects.filter(user=u, is_active=True):
            try:
                send_notification(ch, subject, body)
                self.stdout.write(f"notified via {ch.kind} ({ch.name})")
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"notify via {ch.kind} failed: {exc}")
