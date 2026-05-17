"""MacroSnapshot computation + cache behavior with a fake FRED provider."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.models import MacroSnapshot
from apps.data.providers.fred import MacroObservation
from hedgefund_agents.macro.macro_agent import compute_snapshot

pytestmark = pytest.mark.django_db


class _FakeFred:
    """Returns canned observations and a fixed narrative-free regime."""

    name = "fred"

    def __init__(self):
        self.calls = 0

    def get_latest_value(self, series_id: str, *, as_of: dt.date) -> MacroObservation | None:
        self.calls += 1
        canned = {
            "GDPC1": 22000.0,
            "CPIAUCSL": 310.0,
            "UNRATE": 3.8,
            "DGS10": 4.2,
            "DGS2": 4.4,
            "FEDFUNDS": 5.25,
            "T10Y2Y": -0.2,
            "INDPRO": 102.0,
        }
        return MacroObservation(
            series_id=series_id, date=as_of, vintage_date=as_of,
            value=Decimal(str(canned[series_id])),
        )


def test_snapshot_is_cached_per_date(monkeypatch):
    fake = _FakeFred()
    # Skip the LLM narrative call — return fixed text + tilts.
    monkeypatch.setattr(
        "hedgefund_agents.macro.macro_agent._llm_narrative",
        lambda *, as_of, regime, series_used: ("test narrative", {"technology": "overweight"}),
    )
    d = dt.date(2024, 12, 31)
    s1 = compute_snapshot(d, provider=fake)
    s2 = compute_snapshot(d, provider=fake)
    assert s1.pk == s2.pk
    # First call fetched 8 series; second call hit the cache and fetched none.
    assert fake.calls == 8
    assert MacroSnapshot.objects.count() == 1
    assert s1.policy_stance == "tightening"
    assert s1.yield_curve_state == "inverted"
    assert s1.narrative == "test narrative"
    assert s1.sector_implications == {"technology": "overweight"}
