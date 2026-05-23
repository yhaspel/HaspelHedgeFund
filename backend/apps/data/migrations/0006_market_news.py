# P3-prereq-4: market-news + user news preferences.
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0005_p3_company_profile'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketNewsItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(max_length=16)),
                ('headline', models.CharField(max_length=512)),
                ('summary', models.TextField(blank=True, default='')),
                ('url', models.URLField(max_length=1000)),
                ('image_url', models.URLField(blank=True, default='', max_length=1000)),
                ('source', models.CharField(blank=True, default='', max_length=128)),
                ('published_at', models.DateTimeField(db_index=True)),
                ('symbols', models.JSONField(blank=True, default=list)),
                ('tags', models.JSONField(blank=True, default=list)),
                ('dedup_key', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('sentiment', models.CharField(blank=True, choices=[('bullish', 'Bullish'), ('bearish', 'Bearish'), ('neutral', 'Neutral')], default='', max_length=8)),
                ('sentiment_score', models.FloatField(blank=True, null=True)),
                ('sentiment_rationale', models.CharField(blank=True, default='', max_length=240)),
                ('sentiment_model', models.CharField(blank=True, default='', max_length=128)),
                ('sentiment_at', models.DateTimeField(blank=True, null=True)),
                ('fetched_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'ordering': ['-published_at'],
                'unique_together': {('provider', 'url')},
            },
        ),
        migrations.AddIndex(
            model_name='marketnewsitem',
            index=models.Index(fields=['published_at', 'dedup_key'], name='data_market_publish_b8d074_idx'),
        ),
        migrations.CreateModel(
            name='UserNewsPreferences',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sentiment_enabled', models.BooleanField(default=True)),
                ('sentiment_model', models.CharField(default='openrouter:qwen/qwen3.6-27b', max_length=128)),
                ('chyron_enabled', models.BooleanField(default=True)),
                ('chyron_item_count', models.PositiveSmallIntegerField(default=8)),
                ('feed_item_count', models.PositiveSmallIntegerField(default=20)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=models.deletion.CASCADE, related_name='news_prefs', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
