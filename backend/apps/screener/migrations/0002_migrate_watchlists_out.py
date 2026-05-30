"""P3b: move the single-per-user screener watchlist into apps.watchlists.

Copies every ``screener.Watchlist`` (and its items) into a default
``watchlists.Watchlist`` named the same, then drops the old screener models.
Runs after ``watchlists.0001_initial`` has created the destination tables.

The copy preserves the original ``added_at`` (so the panel's "-added_at"
ordering is unchanged) by writing the timestamp with a queryset ``update`` that
bypasses ``auto_now_add``.
"""
from django.db import migrations


def copy_forward(apps, schema_editor):
    OldWL = apps.get_model("screener", "Watchlist")
    NewWL = apps.get_model("watchlists", "Watchlist")
    NewItem = apps.get_model("watchlists", "WatchlistTicker")

    for owl in OldWL.objects.all().iterator():
        nwl, created = NewWL.objects.get_or_create(
            user_id=owl.user_id,
            name=owl.name or "My Watchlist",
            defaults={"is_default": True},
        )
        if created:
            NewWL.objects.filter(pk=nwl.pk).update(created_at=owl.created_at)
        # Guarantee the user has exactly one default.
        if not NewWL.objects.filter(user_id=owl.user_id, is_default=True).exists():
            NewWL.objects.filter(pk=nwl.pk).update(is_default=True)

        for it in owl.items.all().iterator():
            obj, item_created = NewItem.objects.get_or_create(
                watchlist_id=nwl.pk,
                ticker=it.ticker.upper(),
                defaults={"note": it.note},
            )
            if item_created:
                NewItem.objects.filter(pk=obj.pk).update(added_at=it.added_at)


def copy_backward(apps, schema_editor):
    # Best-effort reverse: the screener tables are recreated by unapplying this
    # migration's schema ops; data is not copied back (the watchlists app is the
    # source of truth post-migration).
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("screener", "0001_initial"),
        ("watchlists", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_forward, copy_backward),
        migrations.DeleteModel(name="WatchlistItem"),
        migrations.DeleteModel(name="Watchlist"),
    ]
