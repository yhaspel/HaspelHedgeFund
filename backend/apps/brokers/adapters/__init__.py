"""Built-in broker adapters. Importing this module registers each adapter
with `apps.brokers.capabilities.register_broker(...)`. The brokers app
config imports this on `ready()` so the registry is populated by the time
views, tasks, or tests reach for it.
"""
from . import mock  # noqa: F401
