"""Persona registry — importing this module registers every persona's
AgentSpec into the versioning registry."""
from . import buffett, burry, damodaran, druckenmiller, graham, lynch, munger, wood

PERSONA_NODES = {
    "buffett": buffett.run_buffett,
    "munger": munger.run_munger,
    "graham": graham.run_graham,
    "wood": wood.run_wood,
    "druckenmiller": druckenmiller.run_druckenmiller,
    "burry": burry.run_burry,
    "damodaran": damodaran.run_damodaran,
    "lynch": lynch.run_lynch,
}

ALL_PERSONAS = list(PERSONA_NODES.keys())

# Persona style categories (P7c Part E — roster-by-fit). VALUE/quality personas
# reason from fundamentals (earnings, intrinsic value) and structurally abstain on
# instruments that have none — ETFs — which dragged the council ETF strategies to
# near-cash (see plan-reviews/research_trend-cta-sector-rotation-alpha §2.1). MACRO
# personas read price/regime and give two-sided ETF signal. Used by
# apps.portfolios.persona_fit to recommend rosters + warn on mis-fit.
VALUE_PERSONAS = frozenset({"buffett", "munger", "graham", "damodaran", "burry"})
GROWTH_PERSONAS = frozenset({"wood", "lynch"})
MACRO_PERSONAS = frozenset({"druckenmiller"})

__all__ = [
    "PERSONA_NODES", "ALL_PERSONAS",
    "VALUE_PERSONAS", "GROWTH_PERSONAS", "MACRO_PERSONAS",
]
