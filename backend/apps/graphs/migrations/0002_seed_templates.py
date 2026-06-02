"""Seed the platform starter templates (Council classic, Sector rotation,
Single persona, Value only). Idempotent."""
from django.db import migrations


def seed(apps, schema_editor):
    from apps.graphs.templates import seed_templates

    seed_templates(apps=apps)


def unseed(apps, schema_editor):
    # No-op on reverse: a template version may have been selected by a Run
    # (PROTECT), so deleting could fail. Templates are harmless to leave behind.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("graphs", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
