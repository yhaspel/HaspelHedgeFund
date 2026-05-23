from django.apps import AppConfig


class BrokersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.brokers"
    label = "brokers"

    def ready(self) -> None:
        # Ensure all built-in adapters register on import.
        from . import adapters  # noqa: F401
