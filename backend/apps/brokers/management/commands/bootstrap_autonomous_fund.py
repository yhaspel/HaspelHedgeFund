"""P7 / P14 — bootstrap the autonomous fund on ONE shared Alpaca paper account.

Reads the single ``ALPACA_PAPER_{NAME,KEY_ID,SECRET}`` env triple (exposed as
``settings.ALPACA_PAPER_ACCOUNTS``) + ``ALPACA_FUND_OWNER_EMAIL`` (or ``--user``).
That one triple IS the fund's shared account — P14 made the fund a shared pool
with per-strategy sleeves, so the old numbered ``ALPACA_PAPER_{1,2,3}_*`` slots
were retired (see ``settings/base.py``). The three §3 template strategies become
fund members with an equal split, each with a disabled ``StrategyAutopilot`` on a
staggered Friday-close cron; with the demo broker (flat $100k book) the fund is
reset so the sleeves are funded and ``/fund`` has a live world.

Note the §3 ``TEMPLATES`` below are *strategy* templates, not env slots: there are
still three of them (three sleeves), all sharing the one account.

**Idempotent + rotation-aware**: NAME is the match key; a changed key/secret
updates the credential and clears ``needs_reauth``. NAMEs must be distinct
(label is not DB-unique). Existing members keep their allocations; the roster
is only ADDED to, never trimmed (the Fund tab owns removals).

Usage:
    uv run python manage.py bootstrap_autonomous_fund
    uv run python manage.py bootstrap_autonomous_fund --user me@example.com
    uv run python manage.py bootstrap_autonomous_fund --broker mock --no-verify  # tests/demo
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.brokers.capabilities import AUTH_NONE, get_capabilities
from apps.brokers.credentials import set_api_key_secret
from apps.brokers.models import BrokerAccount
from apps.portfolios import sleeves
from apps.portfolios.models import (
    AutonomousFund,
    Portfolio,
    PortfolioStrategy,
    StrategyAutopilot,
    Universe,
)

User = get_user_model()

# Per-slot seed templates (§3). ``universe`` is resolved by slug (the existing
# fixtures: sp500_top_200 / sector_etfs / macro_etfs). Staggered Friday-close
# crons (16:30 / 16:45 / 17:00 ET) bound peak LLM concurrency (§3.A).
TEMPLATES = {
    1: {
        "strategy_name": "Multi-Factor Long/Short Equity",
        "universe_slug": "sp500_top_200",
        "cron": "30 16 * * 5",
        "fields": {
            "kind": PortfolioStrategy.KIND_LONG_SHORT,
            "top_k_longs": 15,
            "top_k_shorts": 10,
            "target_net_pct": Decimal("0.50"),
            "target_gross_pct": Decimal("1.30"),
            "max_position_pct": Decimal("0.04"),
            "max_sector_pct": Decimal("0.25"),
            "screener_weights": {
                "momentum_3m": 0.25, "momentum_6m": 0.10, "quality_roic": 0.25,
                "fcf_margin": 0.10, "earnings_yield": 0.20, "debt_to_equity": 0.10,
                "low_vol": 0.15,   # P7 low-vol factor (§3) — built in v1.
            },
            "personas": [
                "buffett", "munger", "graham", "lynch",
                "wood", "druckenmiller", "burry", "damodaran",
            ],
            "auto_run_council": True,
        },
        "short_mode": StrategyAutopilot.SHORT_SINGLE_NAME,
    },
    2: {
        "strategy_name": "Sector Rotation",
        "universe_slug": "sector_etfs",
        "cron": "45 16 * * 5",
        "fields": {
            "kind": PortfolioStrategy.KIND_SECTOR_ROTATION,
            "target_gross_pct": Decimal("1.00"),
            "target_net_pct": Decimal("1.00"),
            "auto_run_council": True,
        },
        "short_mode": StrategyAutopilot.SHORT_CASH,
    },
    3: {
        "strategy_name": "Cross-Asset Trend (CTA-lite)",
        "universe_slug": "macro_etfs",
        "cron": "0 17 * * 5",
        "fields": {
            "kind": PortfolioStrategy.KIND_GLOBAL_MACRO,
            "target_gross_pct": Decimal("1.00"),
            "target_net_pct": Decimal("1.00"),
            "auto_run_council": True,
        },
        "short_mode": StrategyAutopilot.SHORT_CASH,
    },
}


class Command(BaseCommand):
    help = "Bootstrap the shared-account autonomous Alpaca paper fund (P7 / P14)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user", default="",
            help="Owner email (overrides ALPACA_FUND_OWNER_EMAIL).",
        )
        parser.add_argument(
            "--broker", default="alpaca_paper",
            help="Broker code (use 'mock' for demo/tests).",
        )
        parser.add_argument(
            "--slot", type=int, default=0,
            help="Vestigial since the env collapsed to one unnumbered "
                 "ALPACA_PAPER_* triple: there is only slot 1, which is the "
                 "fund's shared account. Kept so injected multi-triple configs "
                 "(the tests) still select. Default 0 = the first complete one.",
        )
        parser.add_argument(
            "--no-verify", action="store_true",
            help="Skip the broker get_account() credential check (no network).",
        )
        parser.add_argument(
            "--seed-validation-backtest", action="store_true",
            help="Also seed a SYNTHETIC demo backtest per strategy (cosmetic only — "
                 "P10 §B3: seeds are not §9-gate evidence; run a real deterministic "
                 "validation backtest to make autopilots enable-able).",
        )

    def handle(self, *args, **opts):
        owner = self._resolve_owner(opts["user"])
        broker_code = opts["broker"]
        cap = get_capabilities(broker_code)
        if cap is None:
            raise CommandError(f"unknown broker code {broker_code!r}")
        is_demo = cap.auth_kind == AUTH_NONE
        verify = (not opts["no_verify"]) and not is_demo

        triples = self._validate_triples()
        if not triples:
            raise CommandError(
                "no complete ALPACA_PAPER_{NAME,KEY_ID,SECRET} triple found "
                "in the environment; nothing to bootstrap."
            )
        fund_slot = opts["slot"] or min(triples)
        if fund_slot not in triples:
            raise CommandError(f"slot {fund_slot} has no complete triple; have {sorted(triples)}.")

        # 1. Accounts: every triple is a paper account; ONE is the fund's pool.
        accounts: dict[int, BrokerAccount] = {}
        for slot, triple in triples.items():
            with transaction.atomic():
                account = self._upsert_account(owner, broker_code, is_demo, triple)
                self._set_credentials(account, triple, is_demo)
            if verify:
                self._verify_account(account)
                account.refresh_from_db()
            accounts[slot] = account
            role = "FUND ACCOUNT" if slot == fund_slot else "paper account (not in fund)"
            self.stdout.write(self.style.SUCCESS(
                f"slot {slot}: {triple['name']!r} → account #{account.id} "
                f"({account.connection_status}) — {role}"
            ))
        fund_account = accounts[fund_slot]

        # 2. Strategies from the §3 templates (one per template, kind per slot).
        seeded_strategies: list[PortfolioStrategy] = []
        for slot in sorted(TEMPLATES):
            tmpl = TEMPLATES[slot]
            universe = Universe.objects.filter(name=tmpl["universe_slug"]).first()
            if universe is None:
                self.stderr.write(self.style.WARNING(
                    f"template {slot}: universe {tmpl['universe_slug']!r} not found — skipping. "
                    "Seed it first (or load fixtures)."
                ))
                continue
            seeded_strategies.append(self._upsert_strategy(owner, universe, tmpl))

        # 3. The fund: shared account + members (equal split, added never trimmed).
        fund = self._upsert_fund(owner, fund_account, seeded_strategies, TEMPLATES)
        self.stdout.write(self.style.SUCCESS(
            f"AutonomousFund #{fund.id} '{fund.name}' on account #{fund_account.id} "
            f"'{fund_account.label}' with {fund.sleeves.filter(is_active=True).count()} "
            f"member strategies (owner {owner.email})."
        ))
        for sl in fund.active_sleeves():
            ap = getattr(sl.strategy, "autopilot", None)
            self.stdout.write(
                f"  · {sl.strategy.name} #{sl.strategy_id}: {sl.allocation_pct}% "
                f"(${sl.initial_capital_usd}), autopilot "
                f"{'enabled' if (ap and ap.is_enabled) else 'disabled'} "
                f"cron {ap.cron_expression if ap else '—'}"
            )

        if opts.get("seed_validation_backtest"):
            from apps.backtests.seed import seed_validation_backtest

            seeded_bt = 0
            for strategy in seeded_strategies:
                bt = seed_validation_backtest(strategy)
                if bt is not None:
                    seeded_bt += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  seeded validation backtest #{bt.id} for '{strategy.name}'"
                    ))
            self.stdout.write(self.style.SUCCESS(
                f"Seeded {seeded_bt} validation backtest(s) — each strategy's "
                "Autopilot enable toggle is now unlocked."
            ))

    # --- resolution -------------------------------------------------------
    def _resolve_owner(self, user_arg: str):
        email = (user_arg or getattr(settings, "ALPACA_FUND_OWNER_EMAIL", "") or "").strip()
        if not email:
            raise CommandError(
                "no owner: pass --user <email> or set ALPACA_FUND_OWNER_EMAIL."
            )
        owner = User.objects.filter(email__iexact=email).first()
        if owner is None:
            raise CommandError(f"no User with email {email!r}.")
        return owner

    def _validate_triples(self) -> dict[int, dict]:
        triples: dict[int, dict] = {}
        names_seen: dict[str, int] = {}
        for acc in getattr(settings, "ALPACA_PAPER_ACCOUNTS", []):
            slot = acc["slot"]
            name, key_id, secret = acc["name"].strip(), acc["key_id"].strip(), acc["secret"].strip()
            present = [bool(name), bool(key_id), bool(secret)]
            if not any(present):
                continue  # slot unused — silent
            if not all(present):
                self.stderr.write(self.style.WARNING(
                    f"slot {slot}: incomplete triple (NAME/KEY_ID/SECRET) — reported, not used."
                ))
                continue
            if name in names_seen:
                raise CommandError(
                    f"duplicate NAME {name!r} (slots {names_seen[name]} and {slot}); "
                    "NAMEs must be distinct (label is not DB-unique)."
                )
            names_seen[name] = slot
            triples[slot] = {"name": name, "key_id": key_id, "secret": secret}
        return triples

    # --- upserts ----------------------------------------------------------
    def _upsert_account(self, owner, broker_code, is_demo, triple) -> BrokerAccount:
        name = triple["name"]
        account = BrokerAccount.objects.filter(
            user=owner, broker=broker_code, label=name,
        ).first()
        if account is not None:
            return account
        cap = get_capabilities(broker_code)
        portfolio = Portfolio.objects.create(
            user=owner,
            name=f"Broker · {cap.display_name} · {name}",
            kind=Portfolio.KIND_BROKER,
            cash_balance=Decimal("100000") if is_demo else Decimal("0"),
        )
        account = BrokerAccount.objects.create(
            user=owner,
            broker=broker_code,
            mode=BrokerAccount.MODE_PAPER,
            account_id=f"{broker_code}:{slugify(name)}",
            label=name,
            base_currency="USD",
            portfolio=portfolio,
            connection_status=(
                BrokerAccount.STATUS_ACTIVE if is_demo else BrokerAccount.STATUS_CONNECTING
            ),
        )
        if is_demo:
            from apps.brokers.adapters.mock import seed_demo_book

            seed_demo_book(account, cash=Decimal("100000"))
        return account

    def _set_credentials(self, account: BrokerAccount, triple, is_demo) -> None:
        if is_demo:
            return  # demo broker is keyless
        set_api_key_secret(account, api_key=triple["key_id"], api_secret=triple["secret"])
        # Rotation-aware: a fresh credential clears a prior needs_reauth so the
        # account is eligible to re-verify.
        if account.connection_status == BrokerAccount.STATUS_NEEDS_REAUTH:
            BrokerAccount.objects.filter(pk=account.pk).update(
                connection_status=BrokerAccount.STATUS_CONNECTING
            )
            account.refresh_from_db()

    def _verify_account(self, account: BrokerAccount) -> None:
        """Probe the broker with the stored creds; flip to ACTIVE or needs_reauth."""
        from apps.brokers.reconcile import get_broker

        try:
            get_broker(account).get_account()
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the bootstrap
            BrokerAccount.objects.filter(pk=account.pk).update(
                connection_status=BrokerAccount.STATUS_NEEDS_REAUTH
            )
            self.stderr.write(self.style.WARNING(
                f"  account #{account.id} credential check failed → needs_reauth: {str(exc)[:160]}"
            ))
            return
        BrokerAccount.objects.filter(pk=account.pk).update(
            connection_status=BrokerAccount.STATUS_ACTIVE
        )

    def _upsert_strategy(self, owner, universe, tmpl) -> PortfolioStrategy:
        name = tmpl["strategy_name"]
        fields = dict(tmpl["fields"])
        strategy = PortfolioStrategy.objects.filter(user=owner, name=name).first()
        if strategy is None:
            book = Portfolio.objects.create(
                user=owner, kind=Portfolio.KIND_STRATEGY, name=name[:64],
            )
            strategy = PortfolioStrategy.objects.create(
                user=owner, name=name, universe=universe, portfolio=book, **fields,
            )
            return strategy
        # Idempotent update of the seeded config (preserves the existing book).
        strategy.universe = universe
        for key, value in fields.items():
            setattr(strategy, key, value)
        strategy.save()
        return strategy

    def _upsert_fund(self, owner, account, strategies, templates) -> AutonomousFund:
        # Resolve the owner's fund exactly the way the API does
        # (``apps.portfolios.api_fund._user_fund``): FIRST fund by id, whatever
        # it is called. Keying on name="Autonomous Fund" meant that once the
        # owner renamed their fund on the Fund tab, a re-run of this
        # "idempotent" command created a SECOND fund bound to the same shared
        # broker account — and the two halves of the product then disagreed
        # about which fund was live.
        fund = AutonomousFund.objects.filter(owner=owner).order_by("id").first()
        if fund is None:
            fund = AutonomousFund.objects.create(owner=owner, name="Autonomous Fund")
        if fund.broker_account_id != account.id:
            try:
                sleeves.configure_account(fund, account)
            except sleeves.FundError as exc:
                raise CommandError(f"cannot bind fund account: {exc.detail}") from exc
            fund.refresh_from_db()
        existing = {sl.strategy_id: sl for sl in fund.sleeves.filter(is_active=True)}
        new = [s for s in strategies if s.pk not in existing]
        if new:
            # Keep existing members' allocations; split the whole 100% equally
            # across the full roster only when the fund is being built fresh.
            if not existing:
                split = sleeves.equal_split(len(new))
                members = [
                    {"strategy_id": s.pk, "allocation_pct": str(pct)}
                    for s, pct in zip(new, split, strict=True)
                ]
            else:
                # Existing members keep their %, newcomers are added at 0% and
                # the owner re-levels on the Fund tab (a bootstrap must never
                # silently reshuffle a running fund's capital).
                members = [
                    {"strategy_id": sid, "allocation_pct": str(sl.allocation_pct)}
                    for sid, sl in existing.items()
                ] + [{"strategy_id": s.pk, "allocation_pct": "0"} for s in new]
            sleeves.set_members(fund, members)
        # Template crons/short modes for the seeded strategies (never auto-enable).
        by_name = {t["strategy_name"]: t for t in templates.values()}
        for strategy in strategies:
            tmpl = by_name.get(strategy.name)
            ap = StrategyAutopilot.objects.filter(strategy=strategy).first()
            if tmpl is None or ap is None:
                continue
            ap.cron_expression = tmpl["cron"]
            ap.short_mode = tmpl["short_mode"]
            ap.model_preset = "frugal"
            ap.save(update_fields=["cron_expression", "short_mode", "model_preset", "updated_at"])
        # Demo broker: the book is a flat $100k — fund the sleeves right away so
        # the seeded world (seed_e2e) has capital to trade.
        fund.refresh_from_db()
        if sleeves.reset_readiness(fund)["ready"] and not any(
            sl.initial_capital_usd for sl in fund.active_sleeves()
        ):
            sleeves.reset_fund(fund)
        return fund
