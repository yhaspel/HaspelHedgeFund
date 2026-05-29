"""Built-in broker adapters. Importing this module registers each adapter
with `apps.brokers.capabilities.register_broker(...)`. The brokers app
config imports this on `ready()` so the registry is populated by the time
views, tasks, or tests reach for it.
"""
from . import (
    alpaca_paper,  # noqa: F401  # P3a-4
    ibkr,  # noqa: F401  # P3a-2
    mock,  # noqa: F401
    tradestation,  # noqa: F401  # P3a-3
)
