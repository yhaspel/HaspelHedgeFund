"""Deterministic interest-ranking for market news (P3-prereq-4).

Neither FMP nor Tiingo exposes a popularity or "interest" signal — both
feeds are ordered by publish time only. This module replaces that gap with
a transparent, deterministic heuristic that the feed header surfaces to
the user honestly ("Ranked by recency, breadth of coverage & source weight").

Score is a weighted sum of four components, each in ``[0, 1]``, weights
summing to 1.0:

  - recency:       exponential decay on the representative's age (weight 0.50)
  - breadth:       cross-provider/source coverage of the cluster (weight 0.25)
  - source_weight: rough reputation of the publisher (weight 0.15)
  - actionability: at least one tagged ticker present (weight 0.10)

Ranking is deliberately **sentiment-independent**: sentiment is produced
*after* ranking, so it cannot feed back into the score (chicken-and-egg).

A *cluster* is the set of rows sharing a ``dedup_key`` — i.e. the same
physical story from one or both providers. The representative is the newest
row in the cluster.

Function is pure (no DB, no wall clock — ``now`` is injected) so it is
trivially testable on fixtures.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .models import MarketNewsItem

# --- Weight constants (sum to 1.0). Tunable; unit-tested individually. ---
WEIGHT_RECENCY = 0.50
WEIGHT_BREADTH = 0.25
WEIGHT_SOURCE = 0.15
WEIGHT_ACTIONABILITY = 0.10

# --- Component constants. ---
RECENCY_HALF_LIFE_H = 18.0
BREADTH_CAP = 4  # diminishing returns above this many member rows
SOURCE_WEIGHT_DEFAULT = 0.5
SOURCE_WEIGHT_KNOWN = 1.0

# Rough major-wire / desk allow-list. Lower-cased substring match. Unknown
# sources don't get penalised hard — they only forgo the boost.
SOURCE_WEIGHTS: dict[str, float] = {
    "reuters": SOURCE_WEIGHT_KNOWN,
    "bloomberg": SOURCE_WEIGHT_KNOWN,
    "ap": SOURCE_WEIGHT_KNOWN,
    "associated press": SOURCE_WEIGHT_KNOWN,
    "wall street journal": SOURCE_WEIGHT_KNOWN,
    "wsj": SOURCE_WEIGHT_KNOWN,
    "financial times": SOURCE_WEIGHT_KNOWN,
    "ft": SOURCE_WEIGHT_KNOWN,
    "cnbc": SOURCE_WEIGHT_KNOWN,
    "marketwatch": SOURCE_WEIGHT_KNOWN,
    "barron": SOURCE_WEIGHT_KNOWN,
    "barron's": SOURCE_WEIGHT_KNOWN,
    "the wall street journal": SOURCE_WEIGHT_KNOWN,
}


@dataclass
class NewsCluster:
    representative: MarketNewsItem
    members: list[MarketNewsItem]
    cluster_size: int
    interest_score: float


def _source_weight(source: str) -> float:
    if not source:
        return SOURCE_WEIGHT_DEFAULT
    needle = source.lower()
    for key, weight in SOURCE_WEIGHTS.items():
        if key in needle:
            return weight
    return SOURCE_WEIGHT_DEFAULT


def _recency(now: dt.datetime, published_at: dt.datetime) -> float:
    delta = now - published_at
    hours = max(delta.total_seconds() / 3600.0, 0.0)
    return 0.5 ** (hours / RECENCY_HALF_LIFE_H)


def _breadth(cluster_size: int) -> float:
    return min(cluster_size, BREADTH_CAP) / BREADTH_CAP


def _actionability(item: MarketNewsItem) -> float:
    return 1.0 if item.symbols else 0.4


def _score(
    *, representative: MarketNewsItem, cluster_size: int, now: dt.datetime
) -> float:
    return (
        WEIGHT_RECENCY * _recency(now, representative.published_at)
        + WEIGHT_BREADTH * _breadth(cluster_size)
        + WEIGHT_SOURCE * _source_weight(representative.source)
        + WEIGHT_ACTIONABILITY * _actionability(representative)
    )


def rank_feed(
    rows: list[MarketNewsItem], *, now: dt.datetime
) -> list[NewsCluster]:
    """Group rows by ``dedup_key`` into clusters, score, sort desc by score.

    Pure — no DB, no clock except the injected ``now``.
    """
    groups: dict[str, list[MarketNewsItem]] = {}
    for row in rows:
        key = row.dedup_key or row.url
        groups.setdefault(key, []).append(row)

    clusters: list[NewsCluster] = []
    for members in groups.values():
        members_sorted = sorted(members, key=lambda r: r.published_at, reverse=True)
        rep = members_sorted[0]
        cs = len(members_sorted)
        score = _score(representative=rep, cluster_size=cs, now=now)
        clusters.append(
            NewsCluster(
                representative=rep,
                members=members_sorted,
                cluster_size=cs,
                interest_score=score,
            )
        )
    clusters.sort(key=lambda c: c.interest_score, reverse=True)
    return clusters
