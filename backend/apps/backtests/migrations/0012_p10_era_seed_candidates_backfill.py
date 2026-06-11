# P10 §B3/§B4 data backfill:
#   * era-tag price-only rows — everything created before the dividend /
#     total-return data fix (PR #50, commit 8f1f107, merged 2026-06-09 15:17 UTC)
#     ran on bars now known to understate returns ~+0.3 Sharpe;
#   * re-status fabricated "[seed]" demo backtests done → synthetic so they
#     stop being eligible §9 autopilot-gate evidence (and become deletable);
#   * force n_candidates=1 on deterministic-engine rows (the deterministic
#     walk-forward ignores the field, so stored 50s were lying).
#
# All three are idempotent and reversible-by-inspection (the reverse is a
# no-op: the forward pass only *narrows* what counts as gate evidence).
import datetime as dt

from django.db import migrations

TOTAL_RETURN_CUTOVER = dt.datetime(2026, 6, 9, 15, 17, tzinfo=dt.UTC)
DETERMINISTIC_ENGINE_MODES = ("risk_parity", "trend", "sector_momentum")


def forward(apps, schema_editor):
    Backtest = apps.get_model("backtests", "Backtest")
    Backtest.objects.filter(created_at__lt=TOTAL_RETURN_CUTOVER).update(
        data_era="price_only"
    )
    Backtest.objects.filter(name__startswith="[seed]", status="done").update(
        status="synthetic"
    )
    Backtest.objects.filter(engine_mode__in=DETERMINISTIC_ENGINE_MODES).exclude(
        n_candidates=1
    ).update(n_candidates=1)


def backward(apps, schema_editor):  # pragma: no cover — see module docstring
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("backtests", "0011_p10_truth_layer"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
