from django.apps import AppConfig


class PortfoliosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portfolios"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        from .seed import seed_default_universe

        post_migrate.connect(seed_default_universe, sender=self)
