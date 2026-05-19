"""EDGAR section extraction must skip the Table of Contents and pick the
real Risk Factors body. Diagnostics (`source_url`, `matched_heading`,
`char_count`) must be persisted on the section_index entry.
"""
from __future__ import annotations

from apps.data.providers.edgar import SECTION_PATTERNS, _locate_section


def _synthetic_10k(risk_body_len: int = 5000) -> str:
    """Return text shaped like a stripped 10-K: TOC with item references
    followed (much later) by the actual item bodies."""
    toc = (
        "TABLE OF CONTENTS "
        "Item 1. Business 3 "
        "Item 1A. Risk Factors 12 "
        "Item 7. Management's Discussion and Analysis 45 "
    )
    body_filler = "BUSINESS BODY " * 200  # ~2600 chars
    risk_body = "We face substantial risks including " + ("competition. " * (risk_body_len // 13))
    mdna_body = "Our results of operations " + ("for the fiscal year improved. " * 100)
    return (
        toc
        + " " * 50
        + "Item 1. Business " + body_filler
        + "Item 1A. Risk Factors " + risk_body
        + "Item 2. Properties properties section. "
        + "Item 7. Management's Discussion and Analysis " + mdna_body
        + "Item 8. Financial Statements end."
    )


def test_risk_factors_skips_table_of_contents() -> None:
    text = _synthetic_10k()
    info = _locate_section(
        "risk_factors", SECTION_PATTERNS["risk_factors"], text, "http://example/filing"
    )
    assert info is not None
    extracted = text[info["start"]: info["end"]]
    # The chosen body must contain the actual risk language, not just the TOC line.
    assert "substantial risks" in extracted
    # Body must be far longer than a TOC entry.
    assert info["char_count"] >= 1500
    # Diagnostics persisted.
    assert info["source_url"] == "http://example/filing"
    assert "risk factors" in info["matched_heading"].lower()


def test_mdna_skips_table_of_contents() -> None:
    text = _synthetic_10k()
    info = _locate_section(
        "mdna", SECTION_PATTERNS["mdna"], text, "http://example/filing"
    )
    assert info is not None
    extracted = text[info["start"]: info["end"]]
    assert "results of operations" in extracted
    assert info["char_count"] >= 1500


def test_section_missing_returns_none() -> None:
    info = _locate_section(
        "risk_factors", SECTION_PATTERNS["risk_factors"], "no items here at all", "u"
    )
    assert info is None
