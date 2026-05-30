"""Materiality gating (P3b) — pure Python, no LLM call.

Decides whether a freshly-completed run is *material enough* to notify, by
comparing it to the most recent prior completed run for the same ticker. Reads
persona ``signal``/``confidence`` from ``AgentMessage.parsed_output`` (NOT
``Decision.action`` — the PM/CIO can downgrade a bullish persona, so the
post-aggregation action is the wrong source for "what did the council think").

Triggers (any one → notify):
  * signal flip (bullish ↔ bearish) vs the prior run
  * aggregate confidence change > 20 points
  * risk-manager veto raised for the first time
  * unanimous bullish (≥ N personas) — on a new ticker or a non-bullish prior
  * a new material news event (materiality ≥ 7) not present in the prior run
"""
from __future__ import annotations

from collections import Counter

from apps.models_catalog.presets import PERSONA_AGENTS
from apps.runs.models import AgentMessage

CONFIDENCE_DELTA = 20
NEWS_MATERIALITY_THRESHOLD = 7.0
UNANIMITY_MIN_PERSONAS = 5


def persona_signals(run) -> list[str]:
    rows = AgentMessage.objects.filter(run=run, agent_name__in=PERSONA_AGENTS)
    out = []
    for m in rows:
        sig = (m.parsed_output or {}).get("signal")
        if sig in ("bullish", "neutral", "bearish"):
            out.append(sig)
    return out


def aggregate_signal(run) -> str:
    sigs = persona_signals(run)
    if not sigs:
        return "neutral"
    c = Counter(sigs)
    bull, bear = c.get("bullish", 0), c.get("bearish", 0)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _decision(run):
    return run.decisions.first()


def run_confidence(run) -> int | None:
    d = _decision(run)
    return int(d.confidence) if d is not None else None


def has_veto(run) -> bool:
    d = _decision(run)
    if d is not None and isinstance(d.risk_overrides, dict) and d.risk_overrides.get("veto"):
        return True
    rm = AgentMessage.objects.filter(run=run, agent_name="risk").first()
    return bool(rm and (rm.parsed_output or {}).get("veto"))


def _news_key(event: dict) -> str:
    return (event.get("headline") or "") + "|" + (event.get("date") or "")


def material_news(run, threshold: float = NEWS_MATERIALITY_THRESHOLD) -> list[dict]:
    nm = AgentMessage.objects.filter(run=run, agent_name="news_digest").first()
    if nm is None:
        return []
    events = (nm.parsed_output or {}).get("material_events") or []
    out = []
    for e in events:
        if isinstance(e, dict):
            try:
                if float(e.get("materiality", 0)) >= threshold:
                    out.append(e)
            except (TypeError, ValueError):
                continue
    return out


def evaluate(current_run, prior_run) -> dict:
    """Return ``{notify, reasons, signal, confidence, n_bullish, n_personas,
    first_seen}`` for ``current_run`` relative to ``prior_run`` (may be None)."""
    sigs = persona_signals(current_run)
    n_personas = len(sigs)
    n_bull = sum(1 for s in sigs if s == "bullish")
    cur_sig = aggregate_signal(current_run)
    cur_conf = run_confidence(current_run)
    cur_veto = has_veto(current_run)
    cur_news = material_news(current_run)
    reasons: list[str] = []

    unanimous = n_personas > 0 and n_bull == n_personas and n_personas >= UNANIMITY_MIN_PERSONAS

    if prior_run is None:
        if unanimous:
            reasons.append(f"unanimous bullish ({n_bull}/{n_personas}) on a new ticker")
        if cur_veto:
            reasons.append("risk manager veto (new ticker)")
        if cur_news:
            reasons.append(f"material news (×{len(cur_news)})")
    else:
        prior_sig = aggregate_signal(prior_run)
        prior_conf = run_confidence(prior_run)
        prior_veto = has_veto(prior_run)
        prior_news = {_news_key(e) for e in material_news(prior_run)}

        if {cur_sig, prior_sig} == {"bullish", "bearish"}:
            reasons.append(f"signal flip: {prior_sig} → {cur_sig}")
        if (
            cur_conf is not None
            and prior_conf is not None
            and abs(cur_conf - prior_conf) > CONFIDENCE_DELTA
        ):
            arrow = "↑" if cur_conf > prior_conf else "↓"
            delta = abs(cur_conf - prior_conf)
            reasons.append(f"confidence {arrow}{delta} ({prior_conf}→{cur_conf})")
        if cur_veto and not prior_veto:
            reasons.append("risk manager veto (first time)")
        if unanimous and prior_sig != "bullish":
            reasons.append(f"unanimous bullish ({n_bull}/{n_personas})")
        new_news = [e for e in cur_news if _news_key(e) not in prior_news]
        if new_news:
            reasons.append(f"new material news (×{len(new_news)})")

    return {
        "notify": bool(reasons),
        "reasons": reasons,
        "signal": cur_sig,
        "confidence": cur_conf,
        "n_bullish": n_bull,
        "n_personas": n_personas,
        "first_seen": prior_run is None,
    }
