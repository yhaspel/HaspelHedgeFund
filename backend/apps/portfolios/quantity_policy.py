"""P3: Quantity rounding/validation for the Manual Book.

The Manual Book stores quantities as ``Decimal`` because broker fills,
fractional brokers, corporate actions, and future asset classes can
produce decimal quantities. The default user-facing policy for
equities/ETFs is still whole shares; ``QuantityPolicy.mode == "fractional"``
opts in to bounded-precision fractional quantities.

This module is the single source of truth for whole-vs-fractional
behaviour: ``apps/portfolios/suggestion.py`` calls it before returning a
suggestion, and the API view layer calls it again before mutating the
book, so frontend display rounding can never be authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Literal

QuantityMode = Literal["whole", "fractional"]

DEFAULT_MAX_DECIMAL_PLACES = 6
WHOLE_QUANTUM = Decimal("1")


@dataclass(frozen=True)
class QuantityPolicy:
    """Capability-style description of what quantities the book accepts.

    P3a will replace ``increment`` with a per-broker / per-account
    capability (some brokers do fractional, others don't, some have a
    1-cent minimum, etc.). The dataclass shape stays the same.
    """

    mode: QuantityMode = "whole"
    increment: Decimal = WHOLE_QUANTUM
    max_decimal_places: int = DEFAULT_MAX_DECIMAL_PLACES

    @staticmethod
    def from_mode(mode: QuantityMode | str | None) -> QuantityPolicy:
        m = (mode or "whole").lower()
        if m == "fractional":
            return QuantityPolicy(
                mode="fractional",
                increment=Decimal("1").scaleb(-DEFAULT_MAX_DECIMAL_PLACES),
                max_decimal_places=DEFAULT_MAX_DECIMAL_PLACES,
            )
        return QuantityPolicy()


@dataclass(frozen=True)
class QuantityResult:
    raw_quantity: Decimal
    quantity: Decimal
    raw_notional: Decimal
    rounded_notional: Decimal
    residual_notional: Decimal
    warning: str | None


def _decimal(value: Decimal | float | int | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_quantity_toward_zero(quantity: Decimal, policy: QuantityPolicy) -> Decimal:
    """Round absolute size toward zero so a suggested open never exceeds caps."""
    q = _decimal(quantity)
    if q == 0:
        return Decimal("0")
    if policy.mode == "whole":
        # Floor magnitude to nearest integer multiple of ``increment``.
        sign = Decimal("-1") if q < 0 else Decimal("1")
        steps = (q.copy_abs() / policy.increment).to_integral_value(rounding=ROUND_DOWN)
        return (sign * steps * policy.increment).quantize(Decimal("1"))
    # fractional
    quant = Decimal("1").scaleb(-policy.max_decimal_places)
    return q.quantize(quant, rounding=ROUND_DOWN)


def round_quantity_for_open(
    raw_quantity: Decimal,
    price: Decimal,
    policy: QuantityPolicy,
) -> QuantityResult:
    """Round an ideal open/increase quantity per ``policy``.

    Returns the accepted ``quantity`` (toward zero), the rounded notional,
    and a residual representing the unfilled portion of the ideal notional.
    Emits a warning if the ideal size is positive but rounds to zero
    under whole-share mode.
    """
    raw = _decimal(raw_quantity)
    p = _decimal(price)
    accepted = round_quantity_toward_zero(raw, policy)
    raw_notional = (raw.copy_abs() * p).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rounded_notional = (accepted.copy_abs() * p).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )
    residual = (raw_notional - rounded_notional).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )
    warning: str | None = None
    if raw.copy_abs() > Decimal("0") and accepted == 0 and policy.mode == "whole":
        warning = (
            "Suggested size is below one share at the latest close. "
            "Increase size or enable fractional shares."
        )
    return QuantityResult(
        raw_quantity=raw,
        quantity=accepted,
        raw_notional=raw_notional,
        rounded_notional=rounded_notional,
        residual_notional=residual,
        warning=warning,
    )


def validate_quantity_for_mode(
    quantity: Decimal, policy: QuantityPolicy, *, allow_zero: bool = False,
) -> Decimal:
    """API-side check: whole-share mode rejects fractional inputs.

    Returns the normalised Decimal on success; raises ``ValueError`` with
    a user-facing message otherwise.
    """
    q = _decimal(quantity)
    if q == 0 and not allow_zero:
        raise ValueError("quantity must be non-zero")
    if policy.mode == "whole":
        if q != q.to_integral_value():
            raise ValueError(
                "whole-share mode rejects fractional quantities; pass "
                "quantity_mode=\"fractional\" to enter fractional shares"
            )
        return q.quantize(Decimal("1"))
    quant = Decimal("1").scaleb(-policy.max_decimal_places)
    return q.quantize(quant, rounding=ROUND_DOWN)
