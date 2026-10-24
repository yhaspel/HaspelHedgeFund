"""CI guard: the dev compose stack (infra/docker-compose.yml) never starts a
local scheduler by default and publishes its ports on loopback only.

* ``beat`` sits behind the ``scheduler`` profile, so ``infra/up.sh`` and a plain
  ``docker compose up`` leave it off: a local beat next to an instance that
  already runs its own would duplicate every scheduled job (emails, LLM runs,
  data pulls). The worker stays in the default stack.
* Every published port binds to 127.0.0.1, and Redis publishes none (the app
  containers reach it over the compose network).

The compose file lives outside backend/, so the guard skips where only backend/
is present (the dev containers mount just backend/ at /app).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parent.parent.parent / "infra" / "docker-compose.yml"

pytestmark = pytest.mark.skipif(
    not COMPOSE.exists(), reason="infra/docker-compose.yml not present (backend-only mount)",
)


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def test_beat_is_opt_in_behind_the_scheduler_profile() -> None:
    services = _services()
    assert services["beat"].get("profiles") == ["scheduler"]
    for name in ("db", "redis", "web", "worker", "frontend"):
        assert "profiles" not in services[name], f"{name} must stay in the default stack"


def test_published_ports_bind_to_loopback_only() -> None:
    for name, service in _services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (
                f"{name} publishes {port!r} on every interface; bind it to 127.0.0.1"
            )


def test_redis_publishes_no_host_port() -> None:
    assert "ports" not in _services()["redis"]
