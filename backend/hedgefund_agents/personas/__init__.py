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

__all__ = ["PERSONA_NODES", "ALL_PERSONAS"]
