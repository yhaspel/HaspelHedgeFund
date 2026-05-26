"""P2n — BYOK for data providers.

Covers the resolver factory (`apps.data.providers.factory`), the
`ALLOW_PLATFORM_DATA_KEYS` gate, the API serializers, the grep-guard against
direct provider instantiation, and an end-to-end round-trip via the
`/api/me/provider-keys/` endpoint.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

User = get_user_model()


# ---------------------------------------------------------------------------
# 1. Resolver factories — basic resolution order.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_factory_caches():
    """The lru_cache lives for the process; clear before/after each test so
    state from one test doesn't leak into another."""
    from apps.data.providers.factory import _reset_caches_for_tests
    _reset_caches_for_tests()
    yield
    _reset_caches_for_tests()


@pytest.mark.django_db
def test_get_fmp_provider_uses_user_byo_key_when_set() -> None:
    from apps.data.providers.factory import get_fmp_provider
    from apps.models_catalog.models import ProviderKey

    u = User.objects.create_user(email="u1@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("fmp", "user-fmp-key-abc")
    pk.save()

    provider = get_fmp_provider(user=u)
    assert provider.api_key == "user-fmp-key-abc"


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=True, FMP_API_KEY="platform-fmp-key")
def test_get_fmp_provider_falls_back_to_platform_when_gate_open() -> None:
    from apps.data.providers.factory import get_fmp_provider

    u = User.objects.create_user(email="u2@x.com", password="x" * 12)
    provider = get_fmp_provider(user=u)
    assert provider.api_key == "platform-fmp-key"


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False, FMP_API_KEY="platform-fmp-key")
def test_get_fmp_provider_raises_when_gate_closed_and_no_user_key() -> None:
    from apps.data.providers.factory import get_fmp_provider

    u = User.objects.create_user(email="u3@x.com", password="x" * 12)
    with pytest.raises(RuntimeError) as exc:
        get_fmp_provider(user=u)
    msg = str(exc.value)
    assert "FMP" in msg
    assert "/settings/models" in msg


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False, FRED_API_KEY="platform-fred-key")
def test_get_fred_provider_falls_back_to_env_even_when_gate_closed() -> None:
    """FRED is in _PUBLIC — public-data exemption stays open in prod."""
    from apps.data.providers.factory import get_fred_provider

    u = User.objects.create_user(email="u4@x.com", password="x" * 12)
    provider = get_fred_provider(user=u)
    assert provider.api_key == "platform-fred-key"


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False, FMP_API_KEY="platform-fmp-key")
def test_get_fmp_provider_force_platform_overrides_gate() -> None:
    """Celery prewarm tasks pass force_platform=True to keep working in prod."""
    from apps.data.providers.factory import get_fmp_provider

    provider = get_fmp_provider(user=None, force_platform=True)
    assert provider.api_key == "platform-fmp-key"


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False, TIINGO_API_KEY="platform-tiingo")
def test_get_tiingo_news_provider_raises_in_prod_without_user_key() -> None:
    from apps.data.providers.factory import get_tiingo_news_provider

    u = User.objects.create_user(email="u5@x.com", password="x" * 12)
    with pytest.raises(RuntimeError) as exc:
        get_tiingo_news_provider(user=u)
    assert "TIINGO" in str(exc.value)


# ---------------------------------------------------------------------------
# 2. NewsService composite behaviour.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False)
def test_get_news_service_returns_only_providers_with_keys() -> None:
    from apps.data.providers.factory import get_news_service
    from apps.models_catalog.models import ProviderKey

    u = User.objects.create_user(email="u6@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("tiingo", "user-tiingo-only")  # FMP NOT set
    pk.save()

    svc = get_news_service(user=u)
    names = sorted(p.name for p in svc._providers)
    assert names == ["tiingo"]


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False)
def test_get_news_service_empty_when_no_keys_and_gate_closed() -> None:
    """Zero providers wired; fetch_and_persist must not raise."""
    from apps.data.providers.factory import get_news_service

    u = User.objects.create_user(email="u7@x.com", password="x" * 12)
    svc = get_news_service(user=u)
    assert svc._providers == []


# ---------------------------------------------------------------------------
# 3. lru_cache invalidation — a freshly rotated key takes effect immediately.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOW_PLATFORM_DATA_KEYS=False)
def test_factory_cache_keys_on_api_key_value() -> None:
    """A user who saves then rotates a key should get a fresh client whose
    api_key reflects the new value."""
    from apps.data.providers.factory import get_fmp_provider
    from apps.models_catalog.models import ProviderKey

    u = User.objects.create_user(email="u8@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("fmp", "first-key")
    pk.save()

    p1 = get_fmp_provider(user=u)
    assert p1.api_key == "first-key"

    pk.set_key("fmp", "rotated-key")
    pk.save()
    p2 = get_fmp_provider(user=u)
    assert p2.api_key == "rotated-key"
    assert p2 is not p1  # different cache entry


# ---------------------------------------------------------------------------
# 4. API round-trip — write / read FMP, Tiingo, FRED keys.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_round_trip_data_keys_set_and_read() -> None:
    u = User.objects.create_user(email="api1@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(u)

    res = client.put(
        "/api/me/provider-keys/",
        {"fmp_api_key": "k-fmp", "tiingo_api_key": "k-ti", "fred_api_key": "k-fr"},
        format="json",
    )
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["fmp"] == "set"
    assert body["tiingo"] == "set"
    assert body["fred"] == "set"
    # plaintext never echoed
    assert "k-fmp" not in res.content.decode()
    assert "k-ti" not in res.content.decode()

    # GET returns the same shape, still no plaintext.
    res2 = client.get("/api/me/provider-keys/")
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["fmp"] == "set"
    assert body2["tiingo"] == "set"
    assert body2["fred"] == "set"
    assert "k-fmp" not in res2.content.decode()


@pytest.mark.django_db
def test_api_clear_data_key_with_empty_string() -> None:
    u = User.objects.create_user(email="api2@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(u)

    client.put("/api/me/provider-keys/", {"fmp_api_key": "k-fmp"}, format="json")
    res = client.put("/api/me/provider-keys/", {"fmp_api_key": ""}, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["fmp"] == "unset"


# ---------------------------------------------------------------------------
# 5. Encryption sanity — saved key is not stored as plaintext on the row.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_saved_fmp_key_is_encrypted_at_rest() -> None:
    from apps.models_catalog.models import ProviderKey

    u = User.objects.create_user(email="enc@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("fmp", "very-secret-fmp-key")
    pk.save()

    row = ProviderKey.objects.get(user=u)
    # _enc column must not contain the plaintext.
    assert "very-secret-fmp-key" not in row.fmp_api_key_enc
    assert len(row.fmp_api_key_enc) > 0
    # but the getter decrypts cleanly:
    assert row.get_key("fmp") == "very-secret-fmp-key"


# ---------------------------------------------------------------------------
# 6. Grep-guard — no bare provider instantiations in production code.
# ---------------------------------------------------------------------------


def test_grep_guard_no_bare_provider_instantiation() -> None:
    """Enforce: only `apps/data/providers/factory.py` and `backend/tests/` may
    construct FmpProvider / TiingoNewsProvider / etc. directly.
    """
    import pytest
    repo_root = Path(__file__).resolve().parent.parent.parent  # → repo root
    # This guard is contract-against-the-checked-in-source — it only makes
    # sense when the working tree is a git repo. Skip when it isn't (e.g.
    # the docker container mounts only /app, not the parent .git).
    check = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        pytest.skip("not running inside a git working tree; grep-guard skipped")
    # Run git grep restricted to backend/ python, excluding factory.py and tests/.
    proc = subprocess.run(
        [
            "git", "grep", "-nE",
            r"(FmpProvider|FredProvider|TiingoNewsProvider|FmpNewsProvider|NewsService)\(",
            "--",
            "backend/**/*.py",
            ":(exclude)backend/apps/data/providers/factory.py",
            ":(exclude)backend/tests/",
            ":(exclude)backend/scripts/",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    # git grep returns 1 when nothing matches — that is what we want.
    matches = proc.stdout.strip()
    # A few lines mention the names in comments / type hints; filter to lines
    # that actually look like constructor calls.
    actual = []
    for line in matches.splitlines():
        # strip "path:line:" prefix
        m = re.match(r"^[^:]+:\d+:(.*)$", line)
        if not m:
            continue
        code = m.group(1)
        # Skip pure type hints, docstrings, comments.
        if code.lstrip().startswith(("#", "*", '"""')):
            continue
        # Skip "FmpProvider | None" type unions / annotations.
        if re.search(r"FmpProvider\s*\|", code):
            continue
        if "FmpProvider]" in code or ": FmpProvider" in code:
            continue
        # Skip the test fixtures' fake instantiations.
        if "api_key=\"fake\"" in code:
            continue
        actual.append(line)
    assert not actual, (
        "Found bare provider instantiations outside factory.py / tests/:\n"
        + "\n".join(actual)
    )


# ---------------------------------------------------------------------------
# 7. Settings gate — ALLOW_PLATFORM_DATA_KEYS exists and defaults False on base.
# ---------------------------------------------------------------------------


def test_allow_platform_data_keys_present_on_settings(settings) -> None:
    assert hasattr(settings, "ALLOW_PLATFORM_DATA_KEYS")
    # In the test settings module it defaults to True; the gate test below
    # exercises the False path via override_settings.
    assert settings.ALLOW_PLATFORM_DATA_KEYS is True


# ---------------------------------------------------------------------------
# 8. Migration smoke — empty default for the new columns.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_new_columns_default_to_empty_string() -> None:
    from apps.models_catalog.models import ProviderKey

    u = User.objects.create_user(email="mig@x.com", password="x" * 12)
    pk = ProviderKey.objects.create(user=u)
    # Defaults exercise the fallthrough policy.
    assert pk.fmp_api_key_enc == ""
    assert pk.tiingo_api_key_enc == ""
    assert pk.fred_api_key_enc == ""
    assert pk.has_key("fmp") is False
    assert pk.has_key("tiingo") is False
    assert pk.has_key("fred") is False
