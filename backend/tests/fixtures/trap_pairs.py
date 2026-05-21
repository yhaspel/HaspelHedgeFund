"""Hand-labelled "trap" pair scenarios for the pair-council quality bar.

A trap pair looks statistically cointegrated (high correlation, low ADF
p-value, wide spread z) but one leg has a real, identifiable bearish
catalyst that explains the divergence — the spread will not revert. A
good council should vote `skip` on these.

The shape of each fixture mirrors the inputs a real cycle would feed
`debate_pair()`: pair identifiers + screener stats + recent headlines.
Headlines are paraphrased archetypes (not direct quotes) of the kinds of
real news that have driven pair-trade traps in the past.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class TrapPairFixture:
    name: str
    leg_a: str                  # statistical long leg (the laggard)
    leg_b: str                  # statistical short leg (the rallier)
    sector: str
    z_current: float            # < 0 in the canonical "long_a / short_b" framing
    correlation: float
    p_value: float
    as_of: date
    news_by_ticker: dict[str, list[str]]
    expected_action: str        # "skip" — the council should refuse to trade
    why_trap: str


TRAP_PAIRS: list[TrapPairFixture] = [
    TrapPairFixture(
        name="earnings_miss_with_guidance_cut",
        leg_a="LEGA",
        leg_b="LEGB",
        sector="Consumer Discretionary",
        z_current=-2.7,
        correlation=0.91,
        p_value=0.03,
        as_of=date(2024, 11, 15),
        news_by_ticker={
            "LEGA": [
                "LEGA reports Q3 revenue miss; cuts FY guidance by 15%.",
                "LEGA CFO departs amid restated quarterly results.",
                "Three analysts downgrade LEGA after disappointing print.",
            ],
            "LEGB": [
                "LEGB beats top and bottom line; raises full-year outlook.",
                "LEGB CEO highlights expanding margins on cost program.",
            ],
        },
        expected_action="skip",
        why_trap=(
            "LEGA's spread divergence is driven by a quarter-specific catalyst "
            "(earnings miss + guidance cut + CFO departure). The reversion bet "
            "is wrong: the laggard has a structural reason to underperform."
        ),
    ),
    TrapPairFixture(
        name="accounting_irregularity",
        leg_a="ACCT",
        leg_b="PEER",
        sector="Financials",
        z_current=-3.1,
        correlation=0.88,
        p_value=0.02,
        as_of=date(2024, 12, 4),
        news_by_ticker={
            "ACCT": [
                "ACCT discloses SEC inquiry into revenue recognition practices.",
                "Audit firm resigns from ACCT, citing material weaknesses.",
                "ACCT delays 10-K filing; class-action lawsuits announced.",
            ],
            "PEER": [
                "PEER announces $1B buyback after strong loan-growth quarter.",
            ],
        },
        expected_action="skip",
        why_trap=(
            "Accounting concerns + auditor resignation + delayed filing = "
            "the cheaper leg is cheap for a reason. Mean reversion would "
            "require the bad news to be revealed as nothing — unlikely."
        ),
    ),
    TrapPairFixture(
        name="secular_demand_shift",
        leg_a="OLDC",
        leg_b="NEWC",
        sector="Communication Services",
        z_current=-2.4,
        correlation=0.86,
        p_value=0.04,
        as_of=date(2024, 10, 22),
        news_by_ticker={
            "OLDC": [
                "OLDC subscriber base shrinks 8% YoY; first decline ever.",
                "Industry analyst: \"OLDC's core market in structural decline.\"",
            ],
            "NEWC": [
                "NEWC user growth accelerates; quarterly DAU up 18%.",
                "NEWC announces partnership with major handset OEM.",
            ],
        },
        expected_action="skip",
        why_trap=(
            "The divergence reflects a secular shift in demand from OLDC's "
            "model to NEWC's. The historical cointegration has broken; the "
            "spread will keep widening, not revert."
        ),
    ),
]


@dataclass
class FairPairFixture:
    """A control: same statistical shape but with benign / neutral headlines.
    A good council should vote `enter` here."""
    name: str
    leg_a: str
    leg_b: str
    sector: str
    z_current: float
    correlation: float
    p_value: float
    as_of: date
    news_by_ticker: dict[str, list[str]]
    expected_action: str


FAIR_PAIRS: list[FairPairFixture] = [
    FairPairFixture(
        name="benign_flow_divergence",
        leg_a="CL",
        leg_b="PG",
        sector="Consumer Staples",
        z_current=-2.6,
        correlation=0.93,
        p_value=0.04,
        as_of=date(2024, 11, 29),
        news_by_ticker={
            "CL": [
                "CL reports in-line Q3 results; reaffirms full-year guidance.",
                "Toothpaste category share unchanged.",
            ],
            "PG": [
                "PG announces routine quarterly dividend.",
                "PG CFO speaks at industry conference; no surprises.",
            ],
        },
        expected_action="enter",
    ),
]
