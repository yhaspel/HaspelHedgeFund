"""P10 §D4 — shared DRF pagination defaults.

Wired explicitly onto the unbounded, monotonically-growing lists (Runs: 386
rows shipped in one response at audit time; Backtests: 55) rather than as a
global ``DEFAULT_PAGINATION_CLASS`` — flipping every list endpoint's response
shape (array → {count, results}) in one phase would churn every store/consumer
for bounded lists that don't need it. New list endpoints that can grow without
bound should set ``pagination_class = DefaultPageNumberPagination``.
"""
from __future__ import annotations

from rest_framework.pagination import PageNumberPagination


class DefaultPageNumberPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200
