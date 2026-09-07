"""BrokerCapabilities descriptor + registry (P3a-1).

The registry is the single source of truth for which brokers exist and what
they can do. The model layer validates `BrokerAccount.broker` and `.mode`
against capabilities; the frontend reads the registry through
`GET /api/brokers/` to render the connect picker without hard-coding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    pass  # circular-safe at type-check time


AUTH_NONE = "none"
AUTH_API_KEY = "api_key"
AUTH_OAUTH2 = "oauth2"
AUTH_OAUTH1 = "oauth1"
AUTH_GATEWAY = "gateway_session"

AUTH_KINDS = (AUTH_NONE, AUTH_API_KEY, AUTH_OAUTH2, AUTH_OAUTH1, AUTH_GATEWAY)


@dataclass(frozen=True)
class BrokerCapabilities:
    code: str
    display_name: str
    auth_kind: str
    supports_paper: bool
    supports_live: bool
    supports_fractional: bool
    quantity_increment: Decimal
    supported_order_types: tuple[str, ...]
    supported_time_in_force: tuple[str, ...]
    # P3a: native bracket / OTO / OCO support. The frontend reads this to
    # offer the bracket affordances; the order-create view reads it to accept
    # an order_class other than "simple". Default False — only Alpaca is True.
    supports_bracket: bool = False
    description: str = ""
    available: bool = True            # False = "ships in a later release"
    community_unverified: bool = False  # Alpaca lifts this in 3a-4 sandbox checklist
    fields: tuple[str, ...] = field(default_factory=tuple)
    # Optional connect-form schema; the demo broker has nothing here.
    connect_form: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "display_name": self.display_name,
            "auth_kind": self.auth_kind,
            "supports_paper": self.supports_paper,
            "supports_live": self.supports_live,
            "supports_fractional": self.supports_fractional,
            "quantity_increment": str(self.quantity_increment),
            "supported_order_types": list(self.supported_order_types),
            "supported_time_in_force": list(self.supported_time_in_force),
            "supports_bracket": self.supports_bracket,
            "description": self.description,
            "available": self.available,
            "community_unverified": self.community_unverified,
            "connect_form": [dict(f) for f in self.connect_form],
        }


# --- Deployment gate (wave 3, WP P3) ----------------------------------------
#
# Registering an adapter says "this code exists"; it does NOT say "this
# integration is supported". IBKR (P3a-2) and TradeStation (P3a-3) are DEFERRED
# phases whose adapters and connect-wizard tiles shipped ahead of the phases
# themselves, so the wizard advertised a capability nobody could actually use.
#
# ``ENABLED_BROKERS`` is the single env-var switch: a broker whose code is not
# in it is served with ``enabled=false`` and cannot back a new BrokerAccount.
# Nothing is deleted — flipping the setting re-enables the whole path — and
# EXISTING accounts on a disabled broker keep working (the gate is on the
# create entry point only, so a live account is never orphaned by a config
# change).

# The "demo" spelling in the setting is an alias for the registered ``mock``
# code, so an operator can write the setting the way the product names it.
BROKER_CODE_ALIASES = {"demo": "mock"}

DEFAULT_ENABLED_BROKERS = ("alpaca_paper", "mock")

# code -> why it is off by default. A code listed here reports
# ``status="deferred"``; anything else switched off reports ``status="disabled"``.
DEFERRED_BROKERS = {
    "ibkr": (
        "Interactive Brokers is a deferred phase (P3a-2). The adapter and the "
        "Client Portal Gateway plumbing exist but the integration has not been "
        "validated end to end, so it cannot be connected yet."
    ),
    "tradestation": (
        "TradeStation is a deferred phase (P3a-3). The OAuth adapter exists but "
        "the integration has not been validated end to end, so it cannot be "
        "connected yet."
    ),
}

STATUS_ENABLED = "enabled"
STATUS_DEFERRED = "deferred"
STATUS_DISABLED = "disabled"
STATUS_UNAVAILABLE = "unavailable"


def normalize_broker_code(code: str) -> str:
    """Resolve a settings/API spelling to the registered adapter code."""
    code = (code or "").strip()
    return BROKER_CODE_ALIASES.get(code, code)


def enabled_broker_codes() -> frozenset[str]:
    """The codes ``ENABLED_BROKERS`` switches on, normalized through the aliases.

    Read through ``getattr`` so the setting is optional; an empty/invalid value
    falls back to the default rather than disabling every broker (a typo in an
    env var must not take the fund offline)."""
    raw = getattr(settings, "ENABLED_BROKERS", None)
    if not raw:
        raw = DEFAULT_ENABLED_BROKERS
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    codes = {normalize_broker_code(str(c)) for c in raw if str(c).strip()}
    return frozenset(codes or {normalize_broker_code(c) for c in DEFAULT_ENABLED_BROKERS})


def is_broker_enabled(code: str) -> bool:
    return normalize_broker_code(code) in enabled_broker_codes()


def broker_status(cap: BrokerCapabilities) -> tuple[str, str]:
    """``(status, note)`` for one capability row. ``note`` is "" when enabled."""
    code = normalize_broker_code(cap.code)
    if not cap.available:
        return STATUS_UNAVAILABLE, cap.description
    if code in enabled_broker_codes():
        return STATUS_ENABLED, ""
    note = DEFERRED_BROKERS.get(code)
    if note is not None:
        return STATUS_DEFERRED, note
    return (
        STATUS_DISABLED,
        f"{cap.display_name} is switched off for this deployment "
        "(ENABLED_BROKERS does not list it).",
    )


def disabled_reason(code: str) -> str | None:
    """The human-readable note for a broker that may NOT be connected, or None
    when it may. Used by ``POST /api/broker-accounts/`` for its 400 body."""
    cap = get_capabilities(normalize_broker_code(code))
    if cap is None:
        return None
    status, note = broker_status(cap)
    return None if status == STATUS_ENABLED else note


def registry_payload() -> list[dict]:
    """``all_capabilities()`` as dicts, each carrying the deployment gate.

    Adds ``enabled`` / ``status`` / ``note`` to the existing capability shape
    (additive — every pre-existing key is unchanged) and orders enabled brokers
    first so the connect wizard leads with what can actually be connected.
    """
    rows = []
    for cap in all_capabilities():
        status, note = broker_status(cap)
        rows.append({
            **cap.to_dict(),
            "enabled": status == STATUS_ENABLED,
            "status": status,
            "note": note,
        })
    rows.sort(key=lambda r: (not r["enabled"], r["code"]))
    return rows


# --- Registry ---------------------------------------------------------------

_REGISTRY: dict[str, BrokerRegistryEntry] = {}


@dataclass(frozen=True)
class BrokerRegistryEntry:
    capabilities: BrokerCapabilities
    adapter_factory: object  # Callable[[BrokerAccount], Broker]; loose typing for migrations


def register_broker(
    capabilities: BrokerCapabilities,
    adapter_factory,
) -> None:
    """Register an adapter at import time. The mock adapter calls this."""
    if capabilities.code in _REGISTRY:
        # Idempotent on re-import (Django autoreload). Overwrite, don't crash.
        _REGISTRY[capabilities.code] = BrokerRegistryEntry(
            capabilities=capabilities, adapter_factory=adapter_factory,
        )
        return
    _REGISTRY[capabilities.code] = BrokerRegistryEntry(
        capabilities=capabilities, adapter_factory=adapter_factory,
    )


def all_capabilities() -> list[BrokerCapabilities]:
    """Capabilities for every registered code plus placeholder real-broker
    tiles. The placeholder tiles let the connect-wizard render the same UI
    before the real-broker phases ship — `available=False` disables the
    create button on the frontend."""
    placeholders = _placeholder_capabilities()
    seen = set(_REGISTRY.keys())
    out = [entry.capabilities for entry in _REGISTRY.values()]
    for cap in placeholders:
        if cap.code in seen:
            continue
        out.append(cap)
    # Stable order: registered first, then placeholders, both alphabetical.
    out.sort(key=lambda c: (not c.available, c.code))
    return out


def get_capabilities(code: str) -> BrokerCapabilities | None:
    entry = _REGISTRY.get(code)
    if entry is not None:
        return entry.capabilities
    for cap in _placeholder_capabilities():
        if cap.code == code:
            return cap
    return None


def get_adapter_factory(code: str):
    entry = _REGISTRY.get(code)
    return entry.adapter_factory if entry is not None else None


def _placeholder_capabilities() -> list[BrokerCapabilities]:
    """Real-broker tiles that future phases will replace with live
    adapters. The IBKR placeholder was removed in P3a-2 when the real
    adapter shipped; TradeStation in P3a-3 (ADR 0012); Alpaca in P3a-4
    (ADR 0013). No placeholders remain."""
    return []
