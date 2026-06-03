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
