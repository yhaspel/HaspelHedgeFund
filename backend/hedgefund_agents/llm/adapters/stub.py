"""Deterministic stub LLM client for Lane-B E2E (E2E_STUB_LLM=1).

Returns a fixed, parseable JSON response instantly so a triggered run/cycle
completes identically every time without spending tokens or hitting a provider.
Wired in `hedgefund_agents/registry.get_llm` behind the `E2E_STUB_LLM` flag,
which is only ever True under the dev/test settings used by `manage.py seed_e2e`
and the nightly Lane-B job. Never frontier, never networked — by construction.
"""
from __future__ import annotations

from ..client import LLMResponse, Message

# A generic, schema-tolerant payload. The agent graph's self-healing /
# node-tolerance (see self_healing_runs) accepts a generic object for agents
# whose exact shape differs, so a council cycle reaches a terminal state.
_STUB_JSON = (
    '{"action": "hold", "confidence": 0.5, "rationale": "E2E stub response — '
    'deterministic, no tokens spent.", "thesis": "E2E stub", "picks": [], '
    '"decisions": [], "summary": "E2E stub"}'
)


class StubLLMClient:
    provider = "stub"

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        return LLMResponse(
            text=_STUB_JSON,
            model=model or "e2e-stub",
            provider="stub",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0,
            finish_reason="stop",
            raw={"stub": True},
        )
