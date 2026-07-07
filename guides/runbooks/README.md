# Incident runbooks (self-host)

Short, practical playbooks for the failure modes a single-operator instance
actually hits. Each is: **symptoms → diagnosis → remediation → prevention.**

All commands assume the compose stack (`docker compose -f infra/docker-compose.yml …`)
and the default `.env.example` credentials.

| Runbook | When |
| --- | --- |
| [runaway-llm-spend.md](./runaway-llm-spend.md) | LLM bill climbing faster than expected |
| [stuck-celery-queue.md](./stuck-celery-queue.md) | Runs/backtests stay "running" and never finish |
| [broker-order-drift.md](./broker-order-drift.md) | Paper positions don't match what the app thinks |
| [data-provider-outage.md](./data-provider-outage.md) | FMP/EDGAR/FRED/Tiingo failing or stale |

If you've configured a notification channel (**Settings → Notifications**), the
operator alerts described in these runbooks are delivered to your active
superusers' email/Telegram automatically.
