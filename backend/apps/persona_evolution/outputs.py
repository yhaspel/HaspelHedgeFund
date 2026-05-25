"""Pydantic schema for the Stage-2 merge/distill LLM call (P3-D §8.3)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PersonaEvolutionOutput(BaseModel):
    """The merge-call output. Char caps are deliberately loose: the prompt
    budget is tighter (1200 / 600), the schema caps are a slightly-looser
    hard ceiling, and code-side bloat truncation is the third defence."""

    market_stance_md: str = Field(default="", max_length=1400)
    general_notes_md: str = Field(default="", max_length=800)
    dropped_facts: list[str] = Field(default_factory=list, max_length=10)
    sources_used: list[str] = Field(default_factory=list, max_length=15)
    material_change: bool = False
