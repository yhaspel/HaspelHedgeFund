"""Full Agent Council LangGraph.

Layout:
    entry
      ├─► fundamentals ─┐
      ├─► technicals   ─┤  (analytical fan-out, parallel)
      ├─► valuation    ─┤
      └─► sentiment    ─┘
                        ▼
                   analytical_join
                        │
            ┌──► buffett ───┐
            ├──► munger ────┤
            ├──► graham ────┤  (persona fan-out, parallel)
            ├──► wood   ────┤
            ├──► druckenmiller ─┤
            ├──► burry ─────┤
            ├──► damodaran ─┤
            └──► lynch  ────┘
                            ▼
                       persona_join
                            │
                            ▼
                       risk_manager
                            │
                            ▼
                       portfolio_manager
                            │
                            ▼
                          END
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..analytical.fundamentals import run_fundamentals
from ..analytical.sentiment import run_sentiment
from ..analytical.technicals import run_technicals
from ..analytical.valuation import run_valuation
from ..base import AgentState
from ..macro.macro_agent import run_macro
from ..news.news_agent import run_news
from ..personas import ALL_PERSONAS, PERSONA_NODES
from ..portfolio.cio import run_cio
from ..portfolio.portfolio_manager import run_portfolio_manager
from ..risk.risk_manager import run_risk_manager

ANALYTICAL_NODES = {
    "fundamentals": run_fundamentals,
    "technicals": run_technicals,
    "valuation": run_valuation,
    "sentiment": run_sentiment,
    "macro": run_macro,
    "news_digest": run_news,
}


def _entry(state: AgentState) -> dict:
    return {}


def _join(state: AgentState) -> dict:
    return {}


def build_council_graph(personas: list[str] | None = None):
    """Build the council graph; `personas` selects a subset (None = all)."""
    selected = list(personas) if personas else list(ALL_PERSONAS)
    invalid = [p for p in selected if p not in PERSONA_NODES]
    if invalid:
        raise ValueError(f"Unknown personas: {invalid}")

    graph = StateGraph(AgentState)
    graph.add_node("entry", _entry)
    graph.add_node("analytical_join", _join)
    graph.add_node("persona_join", _join)
    graph.add_node("risk_manager", run_risk_manager)
    graph.add_node("portfolio_manager", run_portfolio_manager)
    graph.add_node("cio", run_cio)

    for name, fn in ANALYTICAL_NODES.items():
        graph.add_node(name, fn)
        graph.add_edge("entry", name)
        graph.add_edge(name, "analytical_join")

    for name in selected:
        graph.add_node(name, PERSONA_NODES[name])
        graph.add_edge("analytical_join", name)
        graph.add_edge(name, "persona_join")

    graph.set_entry_point("entry")
    graph.add_edge("persona_join", "risk_manager")
    graph.add_edge("risk_manager", "portfolio_manager")
    graph.add_edge("portfolio_manager", "cio")
    graph.add_edge("cio", END)
    return graph.compile()
