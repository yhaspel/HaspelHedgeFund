"""Sentiment stub: with empty NewsBatch, returns neutral score, no LLM call."""
from __future__ import annotations

from unittest.mock import patch

from hedgefund_agents.analytical.sentiment import NewsBatch, run_sentiment


def test_empty_news_returns_neutral_without_llm() -> None:
    state = {"ticker": "AAPL", "news": NewsBatch.empty()}
    with patch("hedgefund_agents.analytical.sentiment.get_llm") as mock_llm:
        result = run_sentiment(state)
    assert result["sentiment"]["score"] == 0.0
    assert result["sentiment"]["top_drivers"] == []
    mock_llm.assert_not_called()


def test_missing_news_treated_as_empty() -> None:
    state = {"ticker": "AAPL"}
    with patch("hedgefund_agents.analytical.sentiment.get_llm") as mock_llm:
        result = run_sentiment(state)
    assert result["sentiment"]["score"] == 0.0
    mock_llm.assert_not_called()
