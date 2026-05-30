import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hedgefund.settings.dev")

app = Celery("hedgefund")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Default periodic schedule. The DatabaseScheduler picks these up at startup
# and any operator-edited PeriodicTask rows override them.
app.conf.beat_schedule = {
    "sweep-orphan-runs": {
        "task": "apps.runs.tasks.sweep_orphan_runs",
        "schedule": 300.0,  # every 5 min
    },
    "sweep-orphan-backtests": {
        "task": "apps.backtests.tasks.sweep_orphan_backtests",
        "schedule": 300.0,  # every 5 min
    },
    "prewarm-macro-snapshot": {
        "task": "apps.data.tasks.prewarm_macro_snapshot",
        "schedule": crontab(minute=15, hour=6),  # 06:15 UTC daily
    },
    # P3a-1: broker reconciliation. `poll_open_orders` is what makes
    # "fill within 30 s" hold; `reconcile_all_accounts` squares residual
    # drift every 5 min.
    "brokers-poll-open-orders": {
        "task": "apps.brokers.tasks.poll_open_orders",
        "schedule": 30.0,
    },
    "brokers-reconcile-accounts": {
        "task": "apps.brokers.tasks.reconcile_all_accounts",
        "schedule": 300.0,
    },
    # P3a-2: tickle the IBKR Client Portal Gateway + reconcile every IBKR
    # account's connection_status against /iserver/auth/status. No-op
    # when no IBKR accounts exist; benign on a deployment without the
    # gateway sidecar (the task flips accounts to needs_reauth and
    # waits for recovery).
    "brokers-ibkr-keep-warm": {
        "task": "apps.brokers.tasks.keep_ibkr_gateway_warm",
        "schedule": 120.0,
    },
    # P3-D: persona evolution. Daily 05:30 UTC tick; cadence (daily/weekly/
    # monthly) is decided inside the task by `is_cycle_due`, so one static
    # schedule serves all three.
    "evolve-personas": {
        "task": "apps.persona_evolution.tasks.evolve_personas",
        "schedule": crontab(minute=30, hour=5),
    },
    # P3b: scheduled-run dispatcher. Fires every minute; finds ScheduledRuns
    # whose next_run_at has passed and hands each to execute_scheduled_run.
    "dispatch-due-scheduled-runs": {
        "task": "apps.schedules.tasks.dispatch_due_scheduled_runs",
        "schedule": 60.0,
    },
    # P3b: nightly leaderboard recompute (agents + strategies). Pure Python.
    "recompute-leaderboards": {
        "task": "apps.leaderboard.tasks.recompute_leaderboards",
        "schedule": crontab(minute=0, hour=7),  # 07:00 UTC daily
    },
}
