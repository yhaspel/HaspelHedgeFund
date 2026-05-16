"""LangGraph: 4-node serial pipeline for Phase 1."""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..analytical.fundamentals import run_fundamentals
from ..analytical.technicals import run_technicals
from ..base import AgentState
from ..personas.buffett import run_buffett
from ..portfolio.trivial_pm import run_trivial_pm


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("fundamentals", run_fundamentals)
    graph.add_node("technicals", run_technicals)
    graph.add_node("buffett", run_buffett)
    graph.add_node("pm", run_trivial_pm)

    graph.set_entry_point("fundamentals")
    graph.add_edge("fundamentals", "technicals")
    graph.add_edge("technicals", "buffett")
    graph.add_edge("buffett", "pm")
    graph.add_edge("pm", END)
    return graph.compile()
