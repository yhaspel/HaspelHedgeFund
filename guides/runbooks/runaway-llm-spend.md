# Runbook: runaway LLM spend

## Symptoms

- Your OpenRouter/Anthropic bill is climbing faster than expected.
- The **Settings → Costs** page shows a spike in daily spend (stacked by model).
- Runs are ending `failed` with `budget_exceeded` (the guard is doing its job) —
  or, worse, they aren't, and spend keeps growing.

## Diagnosis

```bash
# Instance-wide spend, last 30 days, by model + by agent (operator-only endpoint):
curl -s http://localhost:8811/api/costs/summary/?days=30 \
  -H "Authorization: Bearer <operator-JWT>" | python -m json.tool
```

or just open **Settings → Costs**. Look for:

- one **model** dominating (an expensive slug leaked into a preset), or
- one **agent** dominating (a prompt blew up — usually the news/persona path on a
  huge filing), or
- a burst of **runs** (a schedule firing more often than intended).

Check which runs are expensive:

```bash
docker compose -f infra/docker-compose.yml exec -T web uv run python manage.py shell -c \
  "from apps.runs.models import Run; \
   [print(r.id, r.status, r.total_cost_usd) for r in Run.objects.order_by('-total_cost_usd')[:10]]"
```

## Remediation

- **Cap every run.** Set a default backstop in `.env` and restart:
  `RUN_DEFAULT_MAX_BUDGET_USD=5` — any run whose summed LLM cost crosses it aborts
  mid-run (`failed` / `budget_exceeded`). Per-run overrides use `Run.max_budget_usd`.
- **Cap scheduled runs.** Give each schedule a `cost_ceiling_usd` with
  `on_breach = skip` or `degrade` (Settings → the schedule). Breaches alert the
  operator.
- **Pull the expensive model.** Settings → Models: move the offending slug off your
  default/frugal preset; the frugal preset should be a cheap model.
- **Pause schedules** you don't need (Settings → the schedule → deactivate) and
  disable any autopilot that's over-trading.

## Prevention

- Keep `RUN_DEFAULT_MAX_BUDGET_USD` set to a sane ceiling for your usage.
- Watch **Settings → Costs** weekly; it's aggregated from `LLMCall` rows.
- Input truncation already bounds per-call tokens (e.g. 10-K excerpts are clipped
  before the model sees them); the budget guard is the backstop if that ever
  slips.
