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
    # P7: autopilot dispatcher. Fires every minute; finds enabled, non-halted
    # StrategyAutopilots whose next_run_at has passed (the 3 staggered fund
    # accounts) and hands each to run_autopilot_cycle.
    "dispatch-due-autopilots": {
        "task": "apps.portfolios.tasks_autopilot.dispatch_due_autopilots",
        "schedule": 60.0,
    },
    # P7: release locally-held pending_open orders once the market opens.
    "release-pending-open-orders": {
        "task": "apps.portfolios.tasks_autopilot.release_pending_open_orders",
        "schedule": 60.0,
    },
    # P7: hourly guardrail sweep — per-account drawdown auto-halt + (Stage C)
    # fund-level aggregate drawdown halt.
    "autopilot-guardrail-sweep": {
        "task": "apps.portfolios.tasks_autopilot.guardrail_sweep",
        "schedule": 3600.0,
    },
    # P3b: nightly leaderboard recompute (agents + strategies). Pure Python.
    "recompute-leaderboards": {
        "task": "apps.leaderboard.tasks.recompute_leaderboards",
        "schedule": crontab(minute=0, hour=7),  # 07:00 UTC daily
    },
    # P10 §E3: the news-sentiment LAB. Signal supply was page-view-driven and
    # the sleeve had no schedule — both invalidated the forward experiment.
    # Daily symbol-targeted fetch+classify (frozen model), and a weekly cycle
    # Friday 21:30 UTC — after the fund pods' 20:30/20:45/21:00 fires.
    "news-lab-fetch": {
        "task": "apps.portfolios.tasks_lab.fetch_lab_news",
        "schedule": crontab(minute=5, hour=12),  # 12:05 UTC daily
    },
    "news-lab-weekly-cycle": {
        "task": "apps.portfolios.tasks_lab.run_news_lab_cycles",
        "schedule": crontab(minute=30, hour=21, day_of_week=5),  # Fri 21:30 UTC
    },
    # Daily model-catalog reconcile: sync the dev/frugal allowlist, broad-sweep
    # deactivate any OpenRouter row that vanished upstream (the stale-ghost
    # class), and audit pricing drift on every active row — the automatic
    # replacement for the manual Fetch/Verify buttons, keeping the catalog
    # resilient to OpenRouter churn without a deploy.
    "reconcile-model-catalog": {
        "task": "apps.models_catalog.tasks.reconcile_model_catalog",
        "schedule": crontab(minute=45, hour=6),  # 06:45 UTC daily
    },
    # P4: quarterly 13F bulk ingest, a few days after the 45-day deadline
    # (SEC publishes the data sets following mid-Feb/May/Aug/Nov).
    "ingest-13f-datasets": {
        "task": "apps.data.tasks.ingest_13f_current_quarter",
        "schedule": crontab(
            minute=0, hour=8, day_of_month=20, month_of_year="2,5,8,11"
        ),
    },
}
