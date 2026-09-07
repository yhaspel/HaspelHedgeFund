"""Wave 3 / WP P3 — one time-ordered activity feed for the fund.

Everything the fund does is already recorded somewhere; nothing put it on one
timeline, so "did anything happen today?" meant reading four screens. This
module merges, in Python and read-only:

===========================  =========================================================
source row                   entries
===========================  =========================================================
``BrokerOrder``              ``order_pending_open`` (created while the market was
                             shut), ``order_submitted``, ``order_cancelled``,
                             ``order_rejected``
``BrokerFill``               ``order_filled`` (one per fill, so partials show)
``BrokerOrder`` ``flat-``    ``fund_flatten`` — one entry per liquidation batch
``AutopilotRun``             ``autopilot_run`` (dispatch + its outcome, including the
                             ``skipped_pending_open`` and halt-skip audits and the
                             shadow daily-cap evaluation), ``orders_released`` (one
                             per release record written at the open),
                             ``guardrail_transition`` (a transition seen during a
                             dispatch)
``LedgerEntry``              ``fund_reset``, ``sleeve_reallocation`` (rows the sleeve
                             layer tags ``fund sleeve: ...``)
``FundEvent``                ``fund_halt`` / ``fund_resume`` and the guardrail
                             transitions the hourly sweep detects — see the model
                             docstring: those two had no record at all before this,
                             and are written FORWARD only
===========================  =========================================================

Every entry is ``{at, kind, severity, title, detail, links}`` with ``at`` a UTC
ISO-8601 string and ``links`` carrying only the ids it actually has
(``strategy_id`` / ``run_id`` / ``order_id``).

Bounded merge
-------------
Each source is read newest-first and capped (``_source_cap``) before merging, so
one busy source cannot pull an unbounded result set through the API. That makes
the feed a *recent-activity* view, not an exhaustive audit export: an event from
an unusually old row (say a six-month-old order cancelled today) can fall
outside the window. ``has_more`` / ``next_before`` page through it.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AutopilotRun, FundEvent, LedgerEntry

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# Rows pulled per source before the merge. Generous relative to the page size so
# the merged page is complete in practice; see "Bounded merge" above.
SOURCE_CAP_FACTOR = 6
MIN_SOURCE_CAP = 60
MAX_SOURCE_CAP = 600

INFO = FundEvent.SEVERITY_INFO
WARN = FundEvent.SEVERITY_WARN
ERROR = FundEvent.SEVERITY_ERROR


@dataclass
class Entry:
    at: dt.datetime
    kind: str
    severity: str
    title: str
    detail: str = ""
    links: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "at": _iso(self.at),
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "links": {k: v for k, v in self.links.items() if v is not None},
        }


def _iso(value: dt.datetime) -> str:
    """UTC ISO-8601 with a trailing ``Z`` — one timestamp format for the feed."""
    if timezone.is_naive(value):
        value = timezone.make_aware(value, dt.UTC)
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def parse_before(raw: str | None) -> dt.datetime | None:
    """Cursor parser for ``?before=`` — an ISO-8601 instant, else ``None``."""
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt.UTC)
    return parsed


def _source_cap(limit: int) -> int:
    return max(MIN_SOURCE_CAP, min(MAX_SOURCE_CAP, limit * SOURCE_CAP_FACTOR))


def _cut(qs, field_name: str, before: dt.datetime | None):
    """Apply the pagination cursor to one source queryset."""
    return qs if before is None else qs.filter(**{f"{field_name}__lt": before})


def _money(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _qty(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{f:g}"


# ---------------------------------------------------------------------------
# Writers — the only events with no pre-existing home (see FundEvent docstring)
# ---------------------------------------------------------------------------
def record_fund_event(
    fund, kind: str, title: str, *,
    severity: str = INFO, detail: str = "", strategy=None, payload: dict | None = None,
) -> FundEvent | None:
    """Append one ``FundEvent``. Best-effort — audit must never break a halt."""
    try:
        return FundEvent.objects.create(
            fund=fund, strategy=strategy, kind=kind, severity=severity,
            title=title[:200], detail=detail, payload=payload or {},
        )
    except Exception:  # noqa: BLE001 — never raise into the trading/kill path
        log.exception("fund event write failed fund=%s kind=%s", getattr(fund, "pk", None), kind)
        return None


def record_guardrail_transition(autopilot, drawdown: dict) -> FundEvent | None:
    """Record an ``active → soft_cut → halted`` move the hourly sweep found.

    A transition seen during a dispatch already lands on
    ``AutopilotRun.guardrail_actions``; this covers the swept ones, which had no
    record. No-op when the strategy is not a fund member (the feed is
    fund-scoped) or when nothing actually transitioned.
    """
    if not drawdown or not drawdown.get("transition"):
        return None
    from . import sleeves

    strategy = autopilot.strategy
    sleeve = sleeves.sleeve_for(strategy, include_inactive=True)
    if sleeve is None:
        return None
    prior = drawdown.get("prior_state")
    new = drawdown.get("state")
    dd_pct = drawdown.get("drawdown_pct")
    severity = ERROR if new == "halted" else (WARN if new == "soft_cut" else INFO)
    detail = (
        f"Drawdown {dd_pct}% from peak {drawdown.get('peak')} "
        f"(equity {drawdown.get('equity')}) moved {strategy.name} from {prior} to {new}."
    )
    return record_fund_event(
        sleeve.fund, FundEvent.KIND_GUARDRAIL_TRANSITION,
        f"{strategy.name}: guardrail {prior} → {new}",
        severity=severity, detail=detail, strategy=strategy,
        payload={"drawdown": drawdown},
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def _order_entries(fund, before, cap) -> list[Entry]:
    """Order lifecycle: held-for-open, submitted, filled, cancelled, rejected."""
    account = fund.broker_account
    if account is None:
        return []
    from apps.brokers.models import BrokerFill, BrokerOrder

    # Every event of an order is at or after its creation, so cutting on
    # created_at can only drop orders whose events are ALL newer than the
    # cursor — exactly the ones the page must not show.
    orders = list(
        _cut(
            BrokerOrder.objects.filter(broker_account=account),
            "created_at", before,
        )
        .select_related("sleeve")
        .order_by("-created_at")[:cap]
    )
    if not orders:
        return []
    order_ids = [o.id for o in orders]
    run_by_order: dict[int, int] = {}
    try:
        through = AutopilotRun.broker_orders.through.objects.filter(
            brokerorder_id__in=order_ids,
        ).values_list("brokerorder_id", "autopilotrun_id")
        for order_id, run_id in through:
            run_by_order.setdefault(order_id, run_id)
    except Exception:  # noqa: BLE001 — a missing link never breaks the feed
        log.exception("autopilot run link lookup failed fund=%s", fund.pk)

    fills_by_order: dict[int, list] = {}
    for fill in BrokerFill.objects.filter(order_id__in=order_ids).order_by("-filled_at"):
        fills_by_order.setdefault(fill.order_id, []).append(fill)

    entries: list[Entry] = []
    for order in orders:
        sleeve = order.sleeve
        links = {
            "order_id": order.id,
            "strategy_id": sleeve.strategy_id if sleeve is not None else None,
            "run_id": run_by_order.get(order.id),
        }
        who = f"{order.side} {_qty(order.quantity)} {order.ticker}"
        if order.status == BrokerOrder.STATUS_PENDING_OPEN:
            entries.append(Entry(
                at=order.created_at, kind="order_pending_open", severity=INFO,
                title=f"Held for the open: {who}",
                detail=(
                    "Created while the market was closed; the release task submits it at "
                    + (_iso(order.release_after) if order.release_after else "the next open")
                    + "."
                ),
                links=links,
            ))
        if order.submitted_at:
            entries.append(Entry(
                at=order.submitted_at, kind="order_submitted", severity=INFO,
                title=f"Submitted: {who}",
                detail=f"Broker order {order.broker_order_id or '(no id)'} on {account.label}.",
                links=links,
            ))
        for fill in fills_by_order.get(order.id, []):
            entries.append(Entry(
                at=fill.filled_at, kind="order_filled", severity=INFO,
                title=f"Filled: {_qty(fill.quantity)} {order.ticker} @ {_money(fill.price)}",
                detail=(
                    f"{_qty(order.filled_quantity)} of {_qty(order.quantity)} filled; "
                    f"order status {order.status}."
                ),
                links=links,
            ))
        if order.cancelled_at:
            entries.append(Entry(
                at=order.cancelled_at, kind="order_cancelled", severity=WARN,
                title=f"Cancelled: {who}",
                detail=order.error_message or "Cancelled before it filled.",
                links=links,
            ))
        if order.status in (BrokerOrder.STATUS_REJECTED, BrokerOrder.STATUS_ERROR):
            entries.append(Entry(
                at=order.submit_attempted_at or order.created_at,
                kind="order_rejected", severity=ERROR,
                title=f"{order.status.title()}: {who}",
                detail=order.error_message or "The broker refused the order.",
                links=links,
            ))
    return entries


def _flatten_entries(fund, before, cap) -> list[Entry]:
    """One entry per fund-flatten batch, from its ``flat-f<id>-`` orders."""
    account = fund.broker_account
    if account is None:
        return []
    from apps.brokers.models import BrokerOrder

    orders = list(
        _cut(
            BrokerOrder.objects.filter(
                broker_account=account, client_order_id__startswith=f"flat-f{fund.id}-",
            ),
            "created_at", before,
        ).order_by("-created_at")[:cap]
    )
    batches: dict[str, list] = {}
    for order in orders:
        stamp = order.client_order_id.rsplit("-", 1)[-1]
        batches.setdefault(stamp, []).append(order)
    entries: list[Entry] = []
    for stamp, batch in batches.items():
        at = max(o.created_at for o in batch)
        tickers = sorted({o.ticker.upper() for o in batch})
        entries.append(Entry(
            at=at, kind="fund_flatten", severity=WARN,
            title=f"Fund flatten — {len(batch)} closing order(s) queued",
            detail=(
                "Liquidation batch " + stamp + " covering " + ", ".join(tickers[:12])
                + ("…" if len(tickers) > 12 else "")
                + ". Closing orders go through the same gated path as a cycle."
            ),
            links={"order_id": batch[0].id},
        ))
    return entries


def _run_severity(run) -> str:
    if run.status in (AutopilotRun.HALTED, AutopilotRun.FAILED):
        return ERROR
    if run.status == AutopilotRun.SKIPPED:
        return WARN
    return INFO


def _run_detail(run) -> str:
    d = run.submit_decision or {}
    bits: list[str] = []
    if d.get("message"):
        bits.append(str(d["message"]))
    elif d.get("skipped_all") or d.get("skipped"):
        bits.append(f"Skipped: {d.get('skipped_all') or d.get('skipped')}.")
    elif d.get("halted"):
        bits.append(f"Halted: {d['halted']}.")
    elif d.get("disabled"):
        bits.append(f"Disarmed: {d['disabled']}.")
    if d.get("orders") is not None:
        bits.append(
            f"{d.get('orders', 0)} order(s), {d.get('submitted', 0)} submitted, "
            f"{d.get('pending_open', 0)} held for the open."
        )
    caps = d.get("caps_shadow") or {}
    if caps:
        would = caps.get("would_skip")
        if would:
            bits.append(
                f"Shadow daily caps would have skipped {len(would)} order(s): "
                f"{caps.get('reason') or 'cap exceeded'} (shadow mode — nothing was blocked)."
            )
        else:
            bits.append("Shadow daily caps: within limits.")
    if run.error:
        bits.append(run.error[:300])
    return " ".join(bits)


def _run_entries(fund, before, cap) -> list[Entry]:
    """Autopilot dispatches, their release outcomes, and dispatch-time guardrail
    transitions, for every strategy that is (or was) a member of this fund."""
    strategy_ids = list(fund.sleeves.values_list("strategy_id", flat=True))
    if not strategy_ids:
        return []
    runs = list(
        _cut(
            AutopilotRun.objects.filter(autopilot__strategy_id__in=strategy_ids),
            "started_at", before,
        )
        .select_related("autopilot__strategy")
        .order_by("-started_at")[:cap]
    )
    entries: list[Entry] = []
    for run in runs:
        strategy = run.autopilot.strategy
        links = {"run_id": run.id, "strategy_id": strategy.id}
        entries.append(Entry(
            at=run.started_at, kind="autopilot_run", severity=_run_severity(run),
            title=f"{strategy.name}: autopilot {run.status}",
            detail=_run_detail(run) or f"Fired at {_iso(run.fire_time_utc)}.",
            links=links,
        ))
        decision = run.submit_decision or {}
        for record in decision.get("release") or []:
            if not isinstance(record, dict):
                continue
            at = parse_before(record.get("at"))
            if at is None or (before is not None and at >= before):
                continue
            released = record.get("released") or []
            skipped = record.get("skipped") or []
            failed = record.get("failed") or []
            severity = ERROR if failed else (WARN if skipped else INFO)
            entries.append(Entry(
                at=at, kind="orders_released", severity=severity,
                title=(
                    f"{strategy.name}: {len(released)} held order(s) released at the open"
                ),
                detail=(
                    f"{len(released)} released, {len(skipped)} deferred, "
                    f"{len(failed)} rejected/errored at the open."
                ),
                links=links,
            ))
        dd = (run.guardrail_actions or {}).get("drawdown") or {}
        if dd.get("transition"):
            new = dd.get("state")
            severity = ERROR if new == "halted" else (WARN if new == "soft_cut" else INFO)
            entries.append(Entry(
                at=run.started_at, kind="guardrail_transition", severity=severity,
                title=f"{strategy.name}: guardrail {dd.get('prior_state')} → {new}",
                detail=(
                    f"Drawdown {dd.get('drawdown_pct')}% from peak {dd.get('peak')} "
                    "at dispatch time."
                ),
                links=links,
            ))
    return entries


# Notes the sleeve layer stamps on LedgerEntry rows (``_set_sleeve_cash`` /
# ``_wipe_sleeve_positions``), and the feed kind each maps to.
_LEDGER_KINDS = (
    ("fund sleeve: fund reset", "fund_reset", "Fund reset"),
    ("fund sleeve: joined the fund", "sleeve_reallocation", "Sleeve joined the fund"),
    ("fund sleeve: left the fund", "sleeve_reallocation", "Sleeve left the fund"),
    ("fund sleeve: fund account changed", "sleeve_reallocation", "Fund account changed"),
)


def _ledger_entries(fund, before, cap) -> list[Entry]:
    """Fund reset + sleeve reallocation, grouped from the sleeve ledger rows."""
    sleeves_by_pf = {
        sl.portfolio_id: sl
        for sl in fund.sleeves.select_related("strategy").all()
    }
    if not sleeves_by_pf:
        return []
    rows = list(
        _cut(
            LedgerEntry.objects.filter(
                portfolio_id__in=list(sleeves_by_pf), note__startswith="fund sleeve: ",
            ),
            "created_at", before,
        ).order_by("-created_at")[:cap]
    )
    # One ledger row per sleeve (and per wiped position) — group a burst into a
    # single feed entry rather than 30 identical lines.
    groups: dict[tuple, list] = {}
    for row in rows:
        note = row.note or ""
        match = next((m for m in _LEDGER_KINDS if note.startswith(m[0])), None)
        if match is None:
            continue
        bucket = row.created_at.replace(microsecond=0)
        groups.setdefault((match[1], match[2], bucket), []).append(row)
    entries: list[Entry] = []
    for (kind, label, _bucket), batch in groups.items():
        strategies = {
            sleeves_by_pf[r.portfolio_id].strategy.name
            for r in batch if r.portfolio_id in sleeves_by_pf
        }
        strategy_ids = {
            sleeves_by_pf[r.portfolio_id].strategy_id
            for r in batch if r.portfolio_id in sleeves_by_pf
        }
        entries.append(Entry(
            at=max(r.created_at for r in batch), kind=kind, severity=INFO,
            title=f"{label} — {', '.join(sorted(strategies)) or 'fund'}",
            detail=(
                f"{len(batch)} sleeve ledger row(s): "
                + "; ".join(sorted({(r.note or "")[len("fund sleeve: "):] for r in batch})[:4])
            ),
            links={"strategy_id": next(iter(strategy_ids)) if len(strategy_ids) == 1 else None},
        ))
    return entries


def _fund_event_entries(fund, before, cap) -> list[Entry]:
    rows = _cut(
        FundEvent.objects.filter(fund=fund), "created_at", before,
    ).select_related("strategy").order_by("-created_at")[:cap]
    return [
        Entry(
            at=row.created_at, kind=row.kind, severity=row.severity,
            title=row.title, detail=row.detail,
            links={"strategy_id": row.strategy_id},
        )
        for row in rows
    ]


# Events that genuinely have no persisted history before this release. Surfaced
# on the payload so the UI can say "the feed starts here" instead of implying
# the fund never halted.
FORWARD_ONLY_KINDS = ("fund_halt", "fund_resume", "guardrail_transition")


def fund_activity(fund, *, limit: int = DEFAULT_LIMIT, before: dt.datetime | None = None) -> dict:
    """Merged, newest-first activity feed for ``fund``.

    Returns ``{fund_id, limit, before, entries, has_more, next_before, sources,
    notes}``. Pass ``next_before`` back as ``before`` for the next page.
    """
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    cap = _source_cap(limit)
    entries: list[Entry] = []
    for source in (
        _order_entries, _flatten_entries, _run_entries, _ledger_entries,
        _fund_event_entries,
    ):
        try:
            entries.extend(source(fund, before, cap))
        except Exception:  # noqa: BLE001 — one bad source can't blank the feed
            log.exception("fund activity source failed fund=%s src=%s", fund.pk, source.__name__)
    entries = [e for e in entries if e.at is not None]
    if before is not None:
        entries = [e for e in entries if e.at < before]
    entries.sort(key=lambda e: e.at, reverse=True)
    page = entries[:limit]
    has_more = len(entries) > len(page)
    return {
        "fund_id": fund.id,
        "limit": limit,
        "before": _iso(before) if before else None,
        "entries": [e.as_dict() for e in page],
        "has_more": has_more,
        "next_before": _iso(page[-1].at) if page and has_more else None,
        "notes": [
            "Merged read-only from broker orders/fills, autopilot run audits and the "
            "sleeve ledger; only the fund kill switch and swept guardrail transitions "
            "are stored as their own rows.",
            "Those two (" + ", ".join(FORWARD_ONLY_KINDS) + ") are recorded forward only — "
            "halts and swept transitions from before this release were never persisted "
            "anywhere and are not reconstructed here.",
        ],
    }
