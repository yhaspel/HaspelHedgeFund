"""P3b run-history FTS: backfill ``search_text`` for existing runs and add a
Postgres GIN index over ``to_tsvector('english', search_text)``.

The index is created only on PostgreSQL (the sqlite test DB skips it and the
view falls back to an icontains filter). The backfill uses historical models +
inline text extraction so it never imports app code.
"""
from django.db import migrations

_TEXT_KEYS = ("thesis", "digest", "narrative", "rationale", "outlook", "override_reason", "notes")
_LIST_KEYS = ("key_risks", "risk_factor_highlights", "top_drivers", "sentiment_drivers")
_MAX = 20000


def _build(run, AgentMessage, Decision) -> str:
    parts = list(run.tickers or [])
    for d in Decision.objects.filter(run=run):
        parts += [d.ticker, d.action, d.rationale or ""]
    for m in AgentMessage.objects.filter(run=run):
        po = m.parsed_output or {}
        if not isinstance(po, dict):
            continue
        for k in _TEXT_KEYS:
            v = po.get(k)
            if isinstance(v, str) and v:
                parts.append(v)
        for k in _LIST_KEYS:
            v = po.get(k)
            if isinstance(v, list):
                parts += [str(x) for x in v if isinstance(x, str)]
        for ev in po.get("material_events") or []:
            if isinstance(ev, dict) and ev.get("headline"):
                parts.append(str(ev["headline"]))
    return " ".join(p for p in parts if p)[:_MAX]


def forwards(apps, schema_editor):
    Run = apps.get_model("runs", "Run")
    AgentMessage = apps.get_model("runs", "AgentMessage")
    Decision = apps.get_model("runs", "Decision")
    for run in Run.objects.all().iterator():
        txt = _build(run, AgentMessage, Decision)
        if txt and txt != (run.search_text or ""):
            run.search_text = txt
            run.save(update_fields=["search_text"])
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "CREATE INDEX IF NOT EXISTS runs_run_search_gin "
            "ON runs_run USING gin (to_tsvector('english', search_text));"
        )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP INDEX IF EXISTS runs_run_search_gin;")


class Migration(migrations.Migration):
    dependencies = [("runs", "0010_run_search_text")]
    operations = [migrations.RunPython(forwards, backwards)]
