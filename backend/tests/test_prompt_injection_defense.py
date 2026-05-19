"""The News agent's system prompt must explicitly mark filing/news content
as untrusted data and tell the model to ignore embedded instructions.

This is the test counterpart to the master-plan §10 principle about
treating filings/news as data, not instructions — P2b improvement #6 moved
it from "principle" to "explicit test requirement".
"""
from __future__ import annotations

import inspect

from hedgefund_agents.news import news_agent


def test_news_system_prompt_has_injection_defense() -> None:
    src = inspect.getsource(news_agent.run_news)
    lowered = src.lower()
    # The exact phrasing isn't load-bearing — but the prompt must explicitly
    # call out injection / untrusted-data / ignore-instructions.
    assert "untrusted" in lowered or "do not follow" in lowered or "injection" in lowered, (
        "news system prompt must explicitly mark headlines/10-K text as untrusted"
    )
    assert "ignore" in lowered, (
        "news system prompt must instruct the model to ignore instructions "
        "embedded in news/filing content"
    )
