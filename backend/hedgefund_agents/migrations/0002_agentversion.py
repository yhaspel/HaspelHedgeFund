from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hedgefund_agents", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("agent_name", models.CharField(max_length=64)),
                ("version", models.CharField(max_length=32)),
                ("prompt_hash", models.CharField(max_length=64)),
                ("default_model", models.CharField(max_length=128)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("is_current", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"unique_together": {("agent_name", "version")}},
        ),
    ]
