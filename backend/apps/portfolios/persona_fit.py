"""Persona roster-by-fit (P7c Part E).

Encodes the research lesson — value/quality personas reason from fundamentals and
structurally abstain on instruments that have none (ETFs), so they dragged the
council ETF strategies (`global_macro` / `sector_rotation`) to near-cash, while
only macro/trend personas give two-sided ETF signal (see the research §2.1) — as
a roster recommendation + a non-blocking guardrail so the failure can't recur.

Three fit classes:
  * deterministic kinds (risk_parity / pairs / trend / sector_momentum) → council-
    free, no personas;
  * ETF/macro council kinds → macro personas only;
  * single-name equity council kinds → all persona styles fit (every style is a
    valid stock-picker).
"""
from __future__ import annotations

from .models import PortfolioStrategy

# Council kinds whose universe is ETFs/macro instruments (no per-name fundamentals).
ETF_COUNCIL_KINDS = frozenset({
    PortfolioStrategy.KIND_GLOBAL_MACRO,
    PortfolioStrategy.KIND_SECTOR_ROTATION,
})


def recommended_personas(kind: str) -> list[str]:
    """The persona roster that FITS a strategy `kind` (sorted, deterministic)."""
    from hedgefund_agents.personas import ALL_PERSONAS, MACRO_PERSONAS

    if kind in PortfolioStrategy.DETERMINISTIC_KINDS:
        return []
    if kind in ETF_COUNCIL_KINDS:
        return sorted(MACRO_PERSONAS)
    return sorted(ALL_PERSONAS)


def roster_fit_warnings(kind: str, personas: list[str] | None) -> list[str]:
    """Non-blocking warnings for an ill-fitting roster (the documented failure)."""
    from hedgefund_agents.personas import MACRO_PERSONAS, VALUE_PERSONAS

    personas = personas or []
    warnings: list[str] = []
    if kind in PortfolioStrategy.DETERMINISTIC_KINDS and personas:
        warnings.append(
            f"{kind} is deterministic (council-free) — its personas are ignored; "
            "clear them to avoid confusion."
        )
    if kind in ETF_COUNCIL_KINDS:
        misfit = sorted(set(personas) & VALUE_PERSONAS)
        if misfit:
            warnings.append(
                f"Value personas {misfit} reason from fundamentals and structurally "
                f"abstain on ETFs — they dragged council ETF strategies to near-cash. "
                f"Prefer macro personas {sorted(MACRO_PERSONAS)} for this kind."
            )
    return warnings
