"""Build the subject + text/HTML body for a run notification (P3b).

Subject format (per plan):  ``[Hedge Fund] AAPL: 4/5 bullish, ↑18% confidence``
Body: PM action + size, persona signal grid, top material news, a link to the
run detail page, and a "sent because: …" footer.
"""
from __future__ import annotations

from django.conf import settings

from apps.models_catalog.presets import PERSONA_AGENTS
from apps.runs.models import AgentMessage

from .materiality import material_news


def _frontend_base() -> str:
    origins = getattr(settings, "CORS_ALLOWED_ORIGINS", []) or []
    return origins[0] if origins else "http://localhost:4111"


def _persona_grid(run) -> list[tuple[str, str, int]]:
    rows = AgentMessage.objects.filter(run=run, agent_name__in=PERSONA_AGENTS)
    out = []
    for m in rows:
        po = m.parsed_output or {}
        out.append((m.agent_name, po.get("signal", "?"), int(po.get("confidence", 0) or 0)))
    return sorted(out, key=lambda r: (-r[2], r[0]))


def build_content(run, eval_result: dict) -> dict:
    ticker = (run.tickers or ["?"])[0]
    grid = _persona_grid(run)
    n_personas = eval_result.get("n_personas") or len(grid)
    n_bull = eval_result.get("n_bullish") or sum(1 for _, s, _ in grid if s == "bullish")
    conf = eval_result.get("confidence")
    decision = run.decisions.first()
    action = decision.action if decision else "—"

    conf_bit = f", {conf}% confidence" if conf is not None else ""
    subject = f"[Hedge Fund] {ticker}: {n_bull}/{n_personas} bullish{conf_bit}"

    base = _frontend_base()
    link = f"{base}/runs/{run.id}"
    reasons = eval_result.get("reasons") or []

    # ---- plain text ----
    lines = [
        f"{ticker} — PM recommendation: {action.upper()}"
        + (f" (confidence {conf}%)" if conf is not None else ""),
        "",
        "Persona signals:",
    ]
    for name, sig, c in grid:
        lines.append(f"  • {name}: {sig} ({c}%)")
    news = material_news(run)
    if news:
        lines.append("")
        lines.append("Top material news:")
        for e in news[:3]:
            lines.append(f"  • [{e.get('materiality')}] {e.get('headline', '')}")
    lines += [
        "",
        f"Full run: {link}",
        "",
        "Sent because: " + ("; ".join(reasons) if reasons else "scheduled run completed"),
    ]
    text = "\n".join(lines)

    # ---- minimal HTML ----
    grid_html = "".join(
        f"<tr><td>{name}</td><td>{sig}</td><td style='text-align:right'>{c}%</td></tr>"
        for name, sig, c in grid
    )
    news_html = ""
    if news:
        items = "".join(
            f"<li><b>[{e.get('materiality')}]</b> {e.get('headline', '')}</li>"
            for e in news[:3]
        )
        news_html = f"<h4>Top material news</h4><ul>{items}</ul>"
    html = (
        f"<h2>{ticker} — {action.upper()}</h2>"
        + (f"<p>Aggregate confidence: <b>{conf}%</b></p>" if conf is not None else "")
        + f"<h4>Persona signals ({n_bull}/{n_personas} bullish)</h4>"
        + f"<table>{grid_html}</table>"
        + news_html
        + f"<p><a href='{link}'>View the full run →</a></p>"
        + "<hr><p style='color:#888;font-size:12px'>Sent because: "
        + ("; ".join(reasons) if reasons else "scheduled run completed")
        + "</p>"
    )
    return {"subject": subject, "text": text, "html": html}


def build_digest_content(results: list[dict], scheduled_run) -> dict:
    """One consolidated message when a single scheduled fire surfaces many material
    events (plan refinement #4 — digest mode). Each ``result`` is a materiality dict
    carrying ``ticker``, ``run_id``, and ``reasons``."""
    base = _frontend_base()
    n = len(results)
    name = getattr(scheduled_run, "name", "") or "watchlist"
    subject = f"[Hedge Fund] {name}: {n} material updates"

    lines = [f"{n} names on '{name}' had material updates this run:", ""]
    for r in results:
        reasons = "; ".join(r.get("reasons") or []) or "material change"
        lines.append(f"  • {r.get('ticker', '?')}: {reasons} — {base}/runs/{r.get('run_id')}")
    text = "\n".join(lines)

    items = "".join(
        f"<li><b>{r.get('ticker', '?')}</b>: "
        f"{'; '.join(r.get('reasons') or []) or 'material change'} "
        f"(<a href='{base}/runs/{r.get('run_id')}'>run</a>)</li>"
        for r in results
    )
    html = f"<h2>{name}: {n} material updates</h2><ul>{items}</ul>"
    return {"subject": subject, "text": text, "html": html}
