"""Build the denormalized search text for a run (P3b run-history FTS).

``AgentMessage.raw_response`` is never populated, so full-text search runs
against this concatenation of the run's tickers, decision rationales, and the
text-bearing fields of each agent's ``parsed_output`` (persona theses + risks,
news digest, macro narrative, CIO outlook, …).
"""
from __future__ import annotations

_TEXT_KEYS = ("thesis", "digest", "narrative", "rationale", "outlook", "override_reason", "notes")
_LIST_KEYS = ("key_risks", "risk_factor_highlights", "top_drivers", "sentiment_drivers")

MAX_SEARCH_TEXT = 20000


def build_search_text(run) -> str:
    parts: list[str] = list(run.tickers or [])
    for d in run.decisions.all():
        parts += [d.ticker, d.action, d.rationale or ""]
    for m in run.messages.all():
        po = m.parsed_output or {}
        if not isinstance(po, dict):
            continue
        for key in _TEXT_KEYS:
            v = po.get(key)
            if isinstance(v, str) and v:
                parts.append(v)
        for key in _LIST_KEYS:
            v = po.get(key)
            if isinstance(v, list):
                parts += [str(x) for x in v if isinstance(x, str)]
        # news material events: include headlines
        for ev in po.get("material_events") or []:
            if isinstance(ev, dict) and ev.get("headline"):
                parts.append(str(ev["headline"]))
    return " ".join(p for p in parts if p)[:MAX_SEARCH_TEXT]
