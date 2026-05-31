"""P4 WS-4: Fundamentals-agent 13F enrichment.

Patches the node's collaborators (no LLM call, no DB, no network) and captures
the prompts ``run_fundamentals`` builds, asserting:
  (a) seeded ownership -> the USER prompt gains the institutional-ownership
      block and the output can carry the new optional fields;
  (b) no provider / provider returns None / FUNDAMENTALS_USE_13F off ->
      the USER prompt is byte-identical to the no-ownership baseline and the
      new output fields take their defaults;
  (c) a provider whose lookup raises does NOT break the node and adds no block;
  (d) exactly one structured LLM call is made regardless of ownership presence.

``call_structured`` is replaced with a capturing fake so we inspect the exact
``Message`` list the node would have sent, without touching a real LLM. This is
what guarantees cassette parity: existing recorded runs replay only if the USER
prompt is unchanged on the no-ownership path.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest

import hedgefund_agents.analytical.fundamentals as fundamentals_mod
from apps.data.interfaces import FundamentalRow, HolderStake, IssuerOwnershipSummary
from hedgefund_agents.analytical.fundamentals import run_fundamentals
from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.outputs import FundamentalsOutput

AS_OF = dt.date(2024, 12, 31)


def _role(m) -> str:
    """Role of a captured ``Message`` (dataclass/namedtuple or mapping)."""
    return getattr(m, "role", None) or (m["role"] if isinstance(m, dict) else m[0])


def _content(m) -> str:
    """Content/text of a captured ``Message``."""
    val = getattr(m, "content", None)
    if val is None:
        val = getattr(m, "text", None)
    if val is None:
        val = m["content"] if isinstance(m, dict) else m[1]
    return val


class _StubDataProvider:
    """Fundamentals provider returning a fixed two-quarter revenue series."""

    name = "stub"

    def get_fundamentals(self, ticker, metrics, *, as_of, lookback_quarters=8):
        return [
            FundamentalRow(
                ticker=ticker, as_of_date=dt.date(2024, 11, 1),
                period_end=dt.date(2024, 9, 30), metric="revenue",
                value=Decimal("110"),
            ),
            FundamentalRow(
                ticker=ticker, as_of_date=dt.date(2024, 8, 1),
                period_end=dt.date(2024, 6, 30), metric="revenue",
                value=Decimal("100"),
            ),
        ]


class _StubOwnershipProvider:
    """Ownership provider returning a populated summary for any ticker."""

    name = "stub"

    def __init__(self, summary):
        self._summary = summary

    def get_issuer_ownership(self, ticker, *, as_of):
        return self._summary

    def get_filer_portfolio(self, filer_cik, *, as_of):
        return None


class _NoneOwnershipProvider:
    """Ownership provider that always reports no data."""

    name = "none"

    def get_issuer_ownership(self, ticker, *, as_of):
        return None

    def get_filer_portfolio(self, filer_cik, *, as_of):
        return None


class _RaisingOwnershipProvider:
    """Ownership provider whose lookup raises; the node must not propagate it."""

    name = "raising"

    def get_issuer_ownership(self, ticker, *, as_of):
        raise RuntimeError("ownership backend is down")

    def get_filer_portfolio(self, filer_cik, *, as_of):
        raise RuntimeError("ownership backend is down")


def _fake_parsed():
    """A minimal valid FundamentalsOutput the fake LLM hands back by default."""
    return FundamentalsOutput(
        revenue_cagr_3y=0.1, gross_margin=0.4, operating_margin=0.3,
        fcf_margin=0.2, roic=0.15, debt_to_equity=0.5, quality_score=80,
        notes="ok",
    )


class _CapturingCallStructured:
    """Drop-in for ``call_structured`` recording messages + call count.

    Mirrors the real signature: ``(client, *, model, schema, messages,
    max_tokens, cache_ctx)`` -> ``(parsed, LLMResponse)``.
    """

    def __init__(self, parsed=None):
        self._parsed = parsed if parsed is not None else _fake_parsed()
        self.calls: list[dict] = []

    def __call__(self, client, *, model, schema, messages, max_tokens, cache_ctx):
        self.calls.append({"schema": schema, "messages": messages})
        resp = LLMResponse(
            text="{}", model=model, provider="fake",
            prompt_tokens=1, completion_tokens=1, cost_usd=0.0,
        )
        return self._parsed, resp

    def _text_for(self, idx: int, role: str) -> str:
        for m in self.calls[idx]["messages"]:
            if _role(m) == role:
                return _content(m)
        raise AssertionError(f"no {role!r} message captured")

    def system_text(self, idx: int = 0) -> str:
        return self._text_for(idx, "system")

    def user_text(self, idx: int = 0) -> str:
        return self._text_for(idx, "user")


def _summary():
    """A fully populated IssuerOwnershipSummary exercising every block line."""
    return IssuerOwnershipSummary(
        ticker="AAPL",
        period_end=dt.date(2024, 9, 30),
        as_of=AS_OF,
        num_holders=4123,
        total_shares=9_000_000_000,
        total_value_usd=1_500_000_000_000,
        institutional_ownership_pct=61.42,
        ownership_pct=61.42,
        qoq_value_change_pct=3.5,
        top_holders=[
            HolderStake(
                filer_cik="0001067983", filer_name="BERKSHIRE HATHAWAY INC",
                shares=915_560_000, value_usd=174_000_000_000,
                pct_of_portfolio=49.5,
            ),
            HolderStake(
                filer_cik="0000102909", filer_name="VANGUARD GROUP INC",
                shares=1_300_000_000, value_usd=250_000_000_000,
                pct_of_portfolio=4.8,
            ),
        ],
        new_positions=["0001111111", "0002222222"],
        closed_positions=["0003333333"],
        source="edgar",
    )


def _state(*, ownership_provider, ticker="AAPL"):
    """Minimal AgentState for the fundamentals node (collaborators patched)."""
    return {
        "ticker": ticker,
        "as_of_date": AS_OF,
        "data_provider": _StubDataProvider(),
        "ownership_provider": ownership_provider,
    }


def _run(state, cap):
    """Run the node with every external collaborator stubbed out."""
    with patch.object(fundamentals_mod, "call_structured", cap), \
         patch.object(fundamentals_mod, "get_llm", return_value=object()), \
         patch.object(fundamentals_mod, "record_llm_call"), \
         patch("apps.backtests.cache.make_cache_ctx", return_value=None):
        return run_fundamentals(state)


def _baseline_user() -> str:
    """USER prompt with NO ownership data: the pre-13F cassette baseline."""
    cap = _CapturingCallStructured()
    _run(_state(ownership_provider=None), cap)
    return cap.user_text()


# (a) Populated provider -> USER prompt carries the block; output may carry the
#     new optional fields.
def test_populated_ownership_adds_block_to_user_prompt() -> None:
    enriched = FundamentalsOutput(
        revenue_cagr_3y=0.1, gross_margin=0.4, operating_margin=0.3,
        fcf_margin=0.2, roic=0.15, debt_to_equity=0.5, quality_score=85,
        notes="strong",
        institutional_ownership_pct=61.42,
        institutional_ownership_trend="accumulating",
        smart_money_note="Berkshire holds a large stake.",
    )
    cap = _CapturingCallStructured(parsed=enriched)
    result = _run(_state(ownership_provider=_StubOwnershipProvider(_summary())), cap)

    assert len(cap.calls) == 1
    user = cap.user_text()
    assert "Institutional ownership (SEC 13F):" in user
    assert "- Number of 13F holders: 4123" in user
    assert "BERKSHIRE HATHAWAY INC" in user
    assert "- QoQ change in reported value: +3.5%" in user
    # new/closed are list[str] -> rendered as counts via len().
    assert "- Filers that opened a position this quarter: 2" in user
    assert "- Filers that closed a position this quarter: 1" in user
    # Honest caveats are always present.
    assert "~45-day lag" in user
    assert "long US-listed equity" in user

    # The SYSTEM message gains the ownership-framing sentence only when enriched.
    assert "institutional-ownership data" in cap.system_text()

    # Schema unchanged; new optional output fields survive round-trip.
    assert cap.calls[0]["schema"] is FundamentalsOutput
    out = result["fundamentals"]
    assert out["institutional_ownership_trend"] == "accumulating"
    assert out["smart_money_note"] == "Berkshire holds a large stake."
    assert out["institutional_ownership_pct"] == 61.42


# (b) No-data paths: byte-identical USER prompt + default output fields.
def test_no_provider_user_prompt_equals_baseline() -> None:
    baseline = _baseline_user()

    cap = _CapturingCallStructured()
    result = _run(_state(ownership_provider=None), cap)

    assert cap.user_text() == baseline
    assert "Institutional ownership (SEC 13F):" not in cap.user_text()
    assert "institutional-ownership data" not in cap.system_text()
    out = result["fundamentals"]
    assert out["institutional_ownership_pct"] is None
    assert out["institutional_ownership_trend"] == "unknown"
    assert out["smart_money_note"] == ""


def test_provider_returns_none_user_prompt_equals_baseline() -> None:
    baseline = _baseline_user()

    cap = _CapturingCallStructured()
    _run(_state(ownership_provider=_NoneOwnershipProvider()), cap)

    assert cap.user_text() == baseline
    assert "Institutional ownership (SEC 13F):" not in cap.user_text()


def test_flag_off_user_prompt_equals_baseline(settings) -> None:
    baseline = _baseline_user()

    settings.FUNDAMENTALS_USE_13F = False
    cap = _CapturingCallStructured()
    # A populated provider is present, yet the flag suppresses the block.
    _run(_state(ownership_provider=_StubOwnershipProvider(_summary())), cap)

    assert cap.user_text() == baseline
    assert "Institutional ownership (SEC 13F):" not in cap.user_text()


# (c) A raising provider must not break the node, and adds no block.
def test_raising_provider_does_not_break_node() -> None:
    baseline = _baseline_user()

    cap = _CapturingCallStructured()
    result = _run(_state(ownership_provider=_RaisingOwnershipProvider()), cap)

    assert "fundamentals" in result
    assert cap.user_text() == baseline
    assert "Institutional ownership (SEC 13F):" not in cap.user_text()


# (d) Exactly one structured LLM call, regardless of ownership presence.
@pytest.mark.parametrize(
    "make_provider",
    [
        lambda: None,
        lambda: _NoneOwnershipProvider(),
        lambda: _RaisingOwnershipProvider(),
        lambda: _StubOwnershipProvider(_summary()),
    ],
)
def test_exactly_one_llm_call(make_provider) -> None:
    cap = _CapturingCallStructured()
    _run(_state(ownership_provider=make_provider()), cap)
    assert len(cap.calls) == 1
