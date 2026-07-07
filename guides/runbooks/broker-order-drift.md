# Runbook: broker order drift

Trading is **paper-only by design** — live auto-execution is blocked in code — so
"drift" here means your **paper** positions/orders in the app disagree with what
the broker (Alpaca paper) reports.

## Symptoms

- The Portfolio/positions in the app don't match the broker's paper account.
- Orders show as submitted but the broker never filled them (or vice versa).
- Autopilot cards flag an unhealthy account or a stuck order.

## Diagnosis

```bash
# Reconcile one account on demand and see what it changes (account id from the UI):
docker compose -f infra/docker-compose.yml exec -T worker \
  celery -A hedgefund call apps.brokers.tasks.reconcile_account_task --args '[<account_id>]'

# Recent broker/reconcile logs:
docker compose -f infra/docker-compose.yml logs --tail=100 worker | grep -i "reconcile\|broker\|order"
```

`reconcile_account` pulls the broker's truth (positions, fills, order states) and
writes append-only `LedgerEntry` adjustments so the app converges to the broker.
Every order also carries `confirmed_by` / `confirmation_method`, so the audit
trail shows how each one was confirmed.

Common causes: the reconcile task was wedged behind a stuck queue (see
[stuck-celery-queue.md](./stuck-celery-queue.md)); the broker rejected an order
(insufficient buying power, market closed); or a network blip between submit and
confirm.

## Remediation

- **Run reconciliation** (command above, or `reconcile_all_accounts` for all).
  It's idempotent and also runs every 5 minutes on the beat schedule.
- **If the queue is stuck**, fix that first — reconciliation can't run if the
  worker is down.
- **If an order was rejected**, read the rejection reason in the order's audit
  fields, fix the cause (buying power, symbol, session), and let autopilot retry
  on its next cycle — don't hand-place broker orders.
- **Never** flip the paper-only invariant to "fix" drift; drift is a
  reconciliation problem, not an execution one.

## Prevention

- Keep `reconcile-all-accounts` on the beat schedule (default, every 5 min).
- Keep the worker healthy (it does the reconciling).
- Leave `PAPER_AUTO_SUBMIT_ENABLED` and per-schedule `auto_paper_submit` at
  settings you actually intend — surprise submissions are a common source of
  apparent "drift".
