from django.apps import AppConfig


class ModelsCatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.models_catalog"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        from .seed import seed_models

        post_migrate.connect(seed_models, sender=self)
