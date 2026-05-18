"""Sentiment agent (P2a stub).

Real news ingestion arrives in P2b. For P2a we accept a `NewsBatch`
through `state["news"]` if present, otherwise return a neutral score.
No LLM call when the news batch is empty — keeps the graph cheap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import SentimentOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AgentSpec, register


@dataclass(frozen=True)
class NewsBatch:
    headlines: list[str] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> NewsBatch:
        return cls()

    def is_empty(self) -> bool:
        return not self.headlines and not self.summaries


SPEC = AgentSpec(
    agent_name="sentiment",
    version="v1",
    default_model="openrouter:qwen/qwen3.6-27b",
    prompt="(sentiment scorer — see analytical/sentiment.py)",
    config={"kind": "analytical"},
)
register(SPEC)


def run_sentiment(state: AgentState) -> AgentState:
    news: NewsBatch = state.get("news") or NewsBatch.empty()  # type: ignore[assignment]
    if news.is_empty():
        return {"sentiment": SentimentOutput(score=0.0, top_drivers=[]).model_dump()}  # type: ignore[return-value]

    default = DEFAULT_MODELS.get("sentiment", ("openrouter", "qwen/qwen3.6-27b"))
    provider, model = pick_model(state, "sentiment", default)
    client = get_llm(provider)
    system = (
        "You are a financial news sentiment scorer. Given headlines and summaries "
        "about a single company, return a SentimentOutput JSON with a score in "
        "[-1, 1] and up to 5 top_drivers (short phrases)."
    )
    user = (
        "HEADLINES:\n" + "\n".join(news.headlines)
        + "\n\nSUMMARIES:\n" + "\n".join(news.summaries)
    )
    from apps.backtests.cache import make_cache_ctx
    parsed, resp = call_structured(
        client,
        model=model,
        schema=SentimentOutput,
        messages=[Message("system", system), Message("user", user)],
        max_tokens=512,
        cache_ctx=make_cache_ctx(state, "sentiment"),
    )
    record_llm_call(run_id=state.get("run_id"), agent_name="sentiment", resp=resp)
    return {"sentiment": parsed.model_dump()}  # type: ignore[return-value]
