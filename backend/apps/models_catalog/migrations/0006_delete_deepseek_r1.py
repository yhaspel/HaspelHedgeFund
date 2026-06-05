"""Hard-delete the delisted deepseek/deepseek-r1 ModelEntry.

The slug is gone from OpenRouter's catalog and was never wired into a preset or
tier menu — a vestigial seed row that showed as an active/selectable model and
tripped verify_openrouter_pricing. Its flagship-reasoning replacement
(deepseek-v4-pro) now lives in the reasoning allowlist. No FK references
ModelEntry and LLMCall.model is a plain CharField, so the delete is safe and
historical cost audit strings are unaffected. Reverse is a no-op — we don't
resurrect a delisted model on rollback (seed.py no longer re-adds it either).
"""
from django.db import migrations


def delete_deepseek_r1(apps, schema_editor):
    ModelEntry = apps.get_model("models_catalog", "ModelEntry")
    ModelEntry.objects.filter(id="openrouter:deepseek/deepseek-r1").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("models_catalog", "0005_modelentry_supports_reasoning"),
    ]
    operations = [
        migrations.RunPython(delete_deepseek_r1, migrations.RunPython.noop),
    ]
