"""Seed a default 200-name universe for autonomous long-short strategies.

Picked to bias toward liquid, large-cap S&P 500 names so the stub borrow
provider stays sane. Sectors are best-effort tags; the real provider
will replace these in P3a.
"""
from __future__ import annotations

from datetime import date


def _seed():
    return [
        # Technology
        ("AAPL", "Technology"), ("MSFT", "Technology"), ("GOOGL", "Technology"),
        ("GOOG", "Technology"), ("META", "Technology"), ("NVDA", "Technology"),
        ("AVGO", "Technology"), ("ORCL", "Technology"), ("CRM", "Technology"),
        ("ADBE", "Technology"), ("CSCO", "Technology"), ("ACN", "Technology"),
        ("AMD", "Technology"), ("INTC", "Technology"), ("TXN", "Technology"),
        ("QCOM", "Technology"), ("IBM", "Technology"), ("NOW", "Technology"),
        ("INTU", "Technology"), ("PANW", "Technology"), ("AMAT", "Technology"),
        ("LRCX", "Technology"), ("KLAC", "Technology"), ("MU", "Technology"),
        ("ADI", "Technology"), ("SNPS", "Technology"), ("CDNS", "Technology"),
        ("FTNT", "Technology"), ("MSI", "Technology"), ("ADSK", "Technology"),
        # Consumer Discretionary
        ("AMZN", "Consumer Discretionary"), ("TSLA", "Consumer Discretionary"),
        ("HD", "Consumer Discretionary"), ("MCD", "Consumer Discretionary"),
        ("NKE", "Consumer Discretionary"), ("LOW", "Consumer Discretionary"),
        ("SBUX", "Consumer Discretionary"), ("TJX", "Consumer Discretionary"),
        ("BKNG", "Consumer Discretionary"), ("CMG", "Consumer Discretionary"),
        ("ABNB", "Consumer Discretionary"), ("MAR", "Consumer Discretionary"),
        ("ORLY", "Consumer Discretionary"), ("AZO", "Consumer Discretionary"),
        ("YUM", "Consumer Discretionary"), ("ROST", "Consumer Discretionary"),
        ("F", "Consumer Discretionary"), ("GM", "Consumer Discretionary"),
        ("EBAY", "Consumer Discretionary"), ("LULU", "Consumer Discretionary"),
        # Consumer Staples
        ("PG", "Consumer Staples"), ("KO", "Consumer Staples"), ("PEP", "Consumer Staples"),
        ("WMT", "Consumer Staples"), ("COST", "Consumer Staples"),
        ("PM", "Consumer Staples"), ("MO", "Consumer Staples"),
        ("MDLZ", "Consumer Staples"), ("CL", "Consumer Staples"),
        ("TGT", "Consumer Staples"), ("KMB", "Consumer Staples"),
        ("GIS", "Consumer Staples"), ("SYY", "Consumer Staples"),
        ("KHC", "Consumer Staples"), ("HSY", "Consumer Staples"),
        # Financials
        ("BRK.B", "Financials"), ("JPM", "Financials"), ("V", "Financials"),
        ("MA", "Financials"), ("BAC", "Financials"), ("WFC", "Financials"),
        ("MS", "Financials"), ("GS", "Financials"), ("BLK", "Financials"),
        ("AXP", "Financials"), ("C", "Financials"), ("SCHW", "Financials"),
        ("SPGI", "Financials"), ("CB", "Financials"), ("MMC", "Financials"),
        ("PGR", "Financials"), ("PYPL", "Financials"), ("ICE", "Financials"),
        ("AON", "Financials"), ("CME", "Financials"),
        # Health Care
        ("UNH", "Health Care"), ("JNJ", "Health Care"), ("LLY", "Health Care"),
        ("ABBV", "Health Care"), ("MRK", "Health Care"), ("PFE", "Health Care"),
        ("TMO", "Health Care"), ("ABT", "Health Care"), ("DHR", "Health Care"),
        ("BMY", "Health Care"), ("AMGN", "Health Care"), ("CVS", "Health Care"),
        ("ELV", "Health Care"), ("MDT", "Health Care"), ("GILD", "Health Care"),
        ("CI", "Health Care"), ("ISRG", "Health Care"), ("REGN", "Health Care"),
        ("VRTX", "Health Care"), ("BSX", "Health Care"), ("SYK", "Health Care"),
        ("HUM", "Health Care"), ("ZTS", "Health Care"),
        # Communication Services
        ("DIS", "Communication Services"), ("CMCSA", "Communication Services"),
        ("NFLX", "Communication Services"), ("VZ", "Communication Services"),
        ("T", "Communication Services"), ("TMUS", "Communication Services"),
        ("CHTR", "Communication Services"), ("EA", "Communication Services"),
        ("TTWO", "Communication Services"), ("WBD", "Communication Services"),
        # Energy
        ("XOM", "Energy"), ("CVX", "Energy"), ("COP", "Energy"),
        ("SLB", "Energy"), ("EOG", "Energy"), ("MPC", "Energy"),
        ("PSX", "Energy"), ("VLO", "Energy"), ("OXY", "Energy"),
        ("PXD", "Energy"), ("KMI", "Energy"), ("WMB", "Energy"),
        # Industrials
        ("CAT", "Industrials"), ("HON", "Industrials"), ("UPS", "Industrials"),
        ("BA", "Industrials"), ("RTX", "Industrials"), ("DE", "Industrials"),
        ("LMT", "Industrials"), ("UNP", "Industrials"), ("GE", "Industrials"),
        ("MMM", "Industrials"), ("ADP", "Industrials"), ("CSX", "Industrials"),
        ("NSC", "Industrials"), ("FDX", "Industrials"), ("EMR", "Industrials"),
        ("ETN", "Industrials"), ("ITW", "Industrials"), ("WM", "Industrials"),
        ("NOC", "Industrials"), ("GD", "Industrials"),
        # Materials
        ("LIN", "Materials"), ("APD", "Materials"), ("SHW", "Materials"),
        ("ECL", "Materials"), ("FCX", "Materials"), ("NEM", "Materials"),
        ("DD", "Materials"), ("DOW", "Materials"), ("PPG", "Materials"),
        ("CTVA", "Materials"),
        # Utilities
        ("NEE", "Utilities"), ("DUK", "Utilities"), ("SO", "Utilities"),
        ("D", "Utilities"), ("AEP", "Utilities"), ("EXC", "Utilities"),
        ("XEL", "Utilities"), ("SRE", "Utilities"), ("PEG", "Utilities"),
        ("ED", "Utilities"),
        # Real Estate
        ("PLD", "Real Estate"), ("AMT", "Real Estate"), ("EQIX", "Real Estate"),
        ("CCI", "Real Estate"), ("PSA", "Real Estate"), ("SPG", "Real Estate"),
        ("O", "Real Estate"), ("WELL", "Real Estate"), ("DLR", "Real Estate"),
        ("VICI", "Real Estate"),
        # Add a handful of HTB names so the borrow veto can be demonstrated on
        # the seed universe out of the box.
        ("GME", "Consumer Discretionary"), ("AMC", "Communication Services"),
    ]


def seed_default_universe(sender=None, **kwargs):
    from .models import Universe, UniverseMembership
    universe, _ = Universe.objects.get_or_create(
        name="sp500_top_200",
        defaults={
            "description": "Curated ~200 large/mid-cap US equities for autonomous L/S strategies.",
            "source": "manual",
            "is_active": True,
        },
    )
    effective = date(2020, 1, 1)
    seeds = _seed()
    existing = set(
        UniverseMembership.objects.filter(universe=universe).values_list("ticker", flat=True)
    )
    new = []
    for ticker, sector in seeds:
        if ticker in existing:
            continue
        new.append(UniverseMembership(
            universe=universe, ticker=ticker, sector=sector,
            effective_from=effective, effective_to=None,
        ))
    if new:
        UniverseMembership.objects.bulk_create(new)
