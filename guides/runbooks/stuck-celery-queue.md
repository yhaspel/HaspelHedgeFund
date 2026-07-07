# Runbook: stuck Celery queue

## Symptoms

- Runs/backtests sit in `queued` or `running` and never reach `done`/`failed`.
- The **Runs** list shows spinners that never resolve.
- New runs are accepted but nothing appears to execute.

## Diagnosis

```bash
# Is the worker alive and are tasks registered?
docker compose -f infra/docker-compose.yml ps worker
docker compose -f infra/docker-compose.yml exec -T worker celery -A hedgefund inspect ping
docker compose -f infra/docker-compose.yml exec -T worker celery -A hedgefund inspect active

# Is the broker (Redis) reachable?
docker compose -f infra/docker-compose.yml exec -T redis redis-cli ping   # -> PONG

# Recent worker logs (JSON lines carry request_id / run_id for correlation).
docker compose -f infra/docker-compose.yml logs --tail=100 worker
```

Common causes: the worker container is down or crash-looping; Redis is down; a run
hit the wall-clock cap (`RUN_SOFT_TIME_LIMIT_SECONDS`, default 600s) and its
worker child was killed before it could record terminal status (an "orphan").

## Remediation

- **Restart the worker** (also required after backend edits — the worker has no
  autoreload):
  `docker compose -f infra/docker-compose.yml restart worker`
- **Sweep orphans now** — mark abandoned runs failed (this also runs every 60s via
  beat and alerts the operator when it sweeps > 0):
  ```bash
  docker compose -f infra/docker-compose.yml exec -T worker \
    celery -A hedgefund call apps.runs.tasks.sweep_orphan_runs
  ```
- **If Redis is down**, restart it: `docker compose -f infra/docker-compose.yml restart redis`
  (in-flight tasks are lost; re-run them).
- **If beat isn't dispatching** scheduled runs, restart it:
  `docker compose -f infra/docker-compose.yml restart beat`

## Prevention

- Keep `sweep-orphan-runs` on the beat schedule (default): it self-heals orphans
  and alerts you.
- The per-task wall-clock cap guarantees a run terminates even if an LLM route
  stalls — don't remove it.
- After any backend change, restart the worker so it picks up the new code.
