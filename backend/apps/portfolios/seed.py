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


# Sector / thematic ETF registry. Liquidity gates (per plan refinements):
# minimum AUM ~ $500M, minimum ADV ~ $20M, expense_ratio_bps ≤ 75, no leveraged/
# inverse products. regime_affinities indexed by:
#   early_cycle, mid_cycle, late_cycle, recession, rising_rates, sticky_inflation
# Values are hand-curated priors in [-1, 1]; to be revisited after P2c backtest.
SECTOR_ETF_REGISTRY: list[dict] = [
    # SPDR sector ETFs (11)
    {"ticker": "XLK", "sector": "Technology",        "issuer": "SPDR",
     "affinities": {"early_cycle": 0.8, "mid_cycle": 0.5, "late_cycle": -0.2,
                    "recession": -0.4, "rising_rates": -0.5, "sticky_inflation": -0.3}},
    {"ticker": "XLF", "sector": "Financials",        "issuer": "SPDR",
     "affinities": {"early_cycle": 0.3, "mid_cycle": 0.6, "late_cycle": 0.4,
                    "recession": -0.5, "rising_rates": 0.6, "sticky_inflation": 0.0}},
    {"ticker": "XLE", "sector": "Energy",            "issuer": "SPDR",
     "affinities": {"early_cycle": 0.0, "mid_cycle": 0.4, "late_cycle": 0.7,
                    "recession": -0.3, "rising_rates": 0.2, "sticky_inflation": 0.8}},
    {"ticker": "XLV", "sector": "Health Care",       "issuer": "SPDR",
     "affinities": {"early_cycle": 0.0, "mid_cycle": 0.2, "late_cycle": 0.4,
                    "recession": 0.5, "rising_rates": -0.1, "sticky_inflation": 0.0}},
    {"ticker": "XLY", "sector": "Consumer Discretionary", "issuer": "SPDR",
     "affinities": {"early_cycle": 0.7, "mid_cycle": 0.5, "late_cycle": -0.2,
                    "recession": -0.6, "rising_rates": -0.4, "sticky_inflation": -0.4}},
    {"ticker": "XLP", "sector": "Consumer Staples",  "issuer": "SPDR",
     "affinities": {"early_cycle": -0.3, "mid_cycle": 0.0, "late_cycle": 0.3,
                    "recession": 0.6, "rising_rates": -0.2, "sticky_inflation": 0.1}},
    {"ticker": "XLI", "sector": "Industrials",       "issuer": "SPDR",
     "affinities": {"early_cycle": 0.5, "mid_cycle": 0.6, "late_cycle": 0.0,
                    "recession": -0.5, "rising_rates": 0.1, "sticky_inflation": 0.0}},
    {"ticker": "XLU", "sector": "Utilities",         "issuer": "SPDR",
     "affinities": {"early_cycle": -0.4, "mid_cycle": -0.2, "late_cycle": 0.2,
                    "recession": 0.6, "rising_rates": -0.7, "sticky_inflation": -0.2}},
    {"ticker": "XLB", "sector": "Materials",         "issuer": "SPDR",
     "affinities": {"early_cycle": 0.4, "mid_cycle": 0.5, "late_cycle": 0.3,
                    "recession": -0.4, "rising_rates": 0.0, "sticky_inflation": 0.5}},
    {"ticker": "XLRE", "sector": "Real Estate",      "issuer": "SPDR",
     "affinities": {"early_cycle": 0.2, "mid_cycle": 0.3, "late_cycle": -0.1,
                    "recession": -0.2, "rising_rates": -0.8, "sticky_inflation": -0.1}},
    {"ticker": "XLC", "sector": "Communication Services", "issuer": "SPDR",
     "affinities": {"early_cycle": 0.5, "mid_cycle": 0.4, "late_cycle": 0.0,
                    "recession": -0.2, "rising_rates": -0.3, "sticky_inflation": -0.2}},
    # Thematic adds
    {"ticker": "SOXX", "sector": "Technology", "theme": "Semiconductors", "issuer": "iShares",
     "affinities": {"early_cycle": 0.9, "mid_cycle": 0.5, "late_cycle": -0.3,
                    "recession": -0.6, "rising_rates": -0.5, "sticky_inflation": -0.3}},
    {"ticker": "ARKK", "sector": "Technology", "theme": "Disruption", "issuer": "ARK",
     "affinities": {"early_cycle": 0.9, "mid_cycle": 0.2, "late_cycle": -0.5,
                    "recession": -0.7, "rising_rates": -0.9, "sticky_inflation": -0.6}},
    {"ticker": "IBB", "sector": "Health Care", "theme": "Biotech", "issuer": "iShares",
     "affinities": {"early_cycle": 0.4, "mid_cycle": 0.2, "late_cycle": 0.0,
                    "recession": 0.1, "rising_rates": -0.4, "sticky_inflation": -0.1}},
    {"ticker": "KRE", "sector": "Financials", "theme": "Regional Banks", "issuer": "SPDR",
     "affinities": {"early_cycle": 0.4, "mid_cycle": 0.6, "late_cycle": 0.0,
                    "recession": -0.7, "rising_rates": 0.5, "sticky_inflation": -0.1}},
    {"ticker": "ITB", "sector": "Consumer Discretionary", "theme": "Homebuilders",
     "issuer": "iShares",
     "affinities": {"early_cycle": 0.7, "mid_cycle": 0.4, "late_cycle": -0.3,
                    "recession": -0.6, "rising_rates": -0.8, "sticky_inflation": -0.3}},
    {"ticker": "GDX", "sector": "Materials", "theme": "Gold Miners", "issuer": "VanEck",
     "affinities": {"early_cycle": -0.1, "mid_cycle": 0.0, "late_cycle": 0.4,
                    "recession": 0.5, "rising_rates": -0.4, "sticky_inflation": 0.7}},
    {"ticker": "XOP", "sector": "Energy", "theme": "Oil & Gas E&P", "issuer": "SPDR",
     "affinities": {"early_cycle": 0.1, "mid_cycle": 0.4, "late_cycle": 0.7,
                    "recession": -0.3, "rising_rates": 0.2, "sticky_inflation": 0.8}},
    {"ticker": "KWEB", "sector": "Communication Services", "theme": "China Internet",
     "issuer": "KraneShares",
     "affinities": {"early_cycle": 0.3, "mid_cycle": 0.1, "late_cycle": -0.2,
                    "recession": -0.3, "rising_rates": -0.4, "sticky_inflation": -0.2}},
    {"ticker": "TAN", "sector": "Utilities", "theme": "Solar", "issuer": "Invesco",
     "affinities": {"early_cycle": 0.6, "mid_cycle": 0.3, "late_cycle": -0.2,
                    "recession": -0.4, "rising_rates": -0.9, "sticky_inflation": -0.3}},
]


def seed_sector_etf_universe(sender=None, **kwargs):
    from .models import SectorETF, Universe, UniverseMembership
    for entry in SECTOR_ETF_REGISTRY:
        SectorETF.objects.update_or_create(
            ticker=entry["ticker"],
            defaults={
                "sector": entry["sector"],
                "theme": entry.get("theme", ""),
                "issuer": entry.get("issuer", "SPDR"),
                "regime_affinities": entry["affinities"],
                "is_active": True,
            },
        )
    universe, _ = Universe.objects.get_or_create(
        name="sector_etfs",
        defaults={
            "description": "Curated sector + thematic ETFs for sector-rotation strategies.",
            "source": "manual",
            "is_active": True,
        },
    )
    effective = date(2018, 1, 1)
    existing = set(
        UniverseMembership.objects.filter(universe=universe).values_list("ticker", flat=True)
    )
    new = []
    for entry in SECTOR_ETF_REGISTRY:
        if entry["ticker"] in existing:
            continue
        new.append(UniverseMembership(
            universe=universe, ticker=entry["ticker"], sector=entry["sector"],
            effective_from=effective, effective_to=None,
        ))
    if new:
        UniverseMembership.objects.bulk_create(new)


# Macro-expression ETF registry (P2i). Affinities use the same 6-axis encoding
# as SectorETF so the existing macro_regime_vector helper works unchanged.
# Axes: early_cycle, mid_cycle, late_cycle, recession, rising_rates, sticky_inflation.
MACRO_ETF_REGISTRY: list[dict] = [
    # Equity (broad US)
    {"ticker": "SPY", "asset_class": "equity", "direction": "long", "issuer": "SPDR",
     "affinities": {"early_cycle": 0.8, "mid_cycle": 0.7, "late_cycle": 0.0,
                    "recession": -0.7, "rising_rates": -0.2, "sticky_inflation": -0.2}},
    {"ticker": "QQQ", "asset_class": "equity", "direction": "long", "issuer": "Invesco",
     "affinities": {"early_cycle": 0.9, "mid_cycle": 0.6, "late_cycle": -0.2,
                    "recession": -0.7, "rising_rates": -0.6, "sticky_inflation": -0.4}},
    # Inverse equity (no borrow needed)
    {"ticker": "SH", "asset_class": "equity", "direction": "inverse", "inverse_of": "SPY",
     "issuer": "ProShares",
     "affinities": {"early_cycle": -0.8, "mid_cycle": -0.7, "late_cycle": 0.0,
                    "recession": 0.7, "rising_rates": 0.2, "sticky_inflation": 0.2},
     "tracking_note": "Daily-reset inverse ETF; multi-day decay drag."},
    {"ticker": "PSQ", "asset_class": "equity", "direction": "inverse", "inverse_of": "QQQ",
     "issuer": "ProShares",
     "affinities": {"early_cycle": -0.9, "mid_cycle": -0.6, "late_cycle": 0.2,
                    "recession": 0.7, "rising_rates": 0.6, "sticky_inflation": 0.4},
     "tracking_note": "Daily-reset inverse ETF; multi-day decay drag."},
    # Rates / duration
    {"ticker": "TLT", "asset_class": "rates", "direction": "long", "duration_years": 20.0,
     "issuer": "iShares",
     "affinities": {"early_cycle": -0.1, "mid_cycle": -0.2, "late_cycle": 0.4,
                    "recession": 0.8, "rising_rates": -0.9, "sticky_inflation": -0.5},
     "tracking_note": "Proxy for 20y+ Treasuries; credit-spread and liquidity drag vs underlying."},
    {"ticker": "IEF", "asset_class": "rates", "direction": "long", "duration_years": 8.0,
     "issuer": "iShares",
     "affinities": {"early_cycle": 0.0, "mid_cycle": -0.1, "late_cycle": 0.3,
                    "recession": 0.6, "rising_rates": -0.7, "sticky_inflation": -0.4}},
    {"ticker": "SHY", "asset_class": "rates", "direction": "long", "duration_years": 2.0,
     "issuer": "iShares",
     "affinities": {"early_cycle": 0.0, "mid_cycle": 0.0, "late_cycle": 0.1,
                    "recession": 0.3, "rising_rates": -0.2, "sticky_inflation": -0.1}},
    {"ticker": "TBT", "asset_class": "rates", "direction": "inverse", "inverse_of": "TLT",
     "issuer": "ProShares",
     "affinities": {"early_cycle": 0.1, "mid_cycle": 0.2, "late_cycle": -0.4,
                    "recession": -0.8, "rising_rates": 0.9, "sticky_inflation": 0.5},
     "tracking_note": "Daily-reset inverse + 2x leveraged; aggressive decay."},
    # Inflation hedges
    {"ticker": "TIP", "asset_class": "inflation", "direction": "long", "issuer": "iShares",
     "affinities": {"early_cycle": 0.0, "mid_cycle": 0.1, "late_cycle": 0.3,
                    "recession": 0.0, "rising_rates": -0.4, "sticky_inflation": 0.7}},
    {"ticker": "GLD", "asset_class": "commodity", "direction": "long", "issuer": "SPDR",
     "affinities": {"early_cycle": 0.0, "mid_cycle": 0.0, "late_cycle": 0.3,
                    "recession": 0.5, "rising_rates": -0.4, "sticky_inflation": 0.8}},
    {"ticker": "DBC", "asset_class": "commodity", "direction": "long", "issuer": "Invesco",
     "affinities": {"early_cycle": 0.1, "mid_cycle": 0.4, "late_cycle": 0.6,
                    "recession": -0.3, "rising_rates": 0.2, "sticky_inflation": 0.8}},
    # FX proxies
    {"ticker": "UUP", "asset_class": "fx_proxy", "direction": "long", "issuer": "Invesco",
     "affinities": {"early_cycle": -0.1, "mid_cycle": 0.0, "late_cycle": 0.3,
                    "recession": 0.4, "rising_rates": 0.7, "sticky_inflation": 0.2},
     "tracking_note": "USD Bullish basket; not a true FX position."},
    {"ticker": "UDN", "asset_class": "fx_proxy", "direction": "long", "issuer": "Invesco",
     "affinities": {"early_cycle": 0.1, "mid_cycle": 0.0, "late_cycle": -0.3,
                    "recession": -0.4, "rising_rates": -0.7, "sticky_inflation": -0.2},
     "tracking_note": "USD Bearish basket."},
    # EM
    {"ticker": "EEM", "asset_class": "em", "direction": "long", "issuer": "iShares",
     "affinities": {"early_cycle": 0.7, "mid_cycle": 0.5, "late_cycle": -0.1,
                    "recession": -0.7, "rising_rates": -0.5, "sticky_inflation": 0.0}},
]


def seed_macro_etf_universe(sender=None, **kwargs):
    from .models import MacroETF, Universe, UniverseMembership
    for entry in MACRO_ETF_REGISTRY:
        MacroETF.objects.update_or_create(
            ticker=entry["ticker"],
            defaults={
                "asset_class": entry["asset_class"],
                "direction": entry.get("direction", "long"),
                "inverse_of": entry.get("inverse_of", ""),
                "duration_years": entry.get("duration_years"),
                "issuer": entry.get("issuer", ""),
                "regime_affinities": entry["affinities"],
                "tracking_note": entry.get("tracking_note", ""),
                "is_active": True,
            },
        )
    universe, _ = Universe.objects.get_or_create(
        name="macro_etfs",
        defaults={
            "description": "Curated macro-expression ETFs for global-macro strategies.",
            "source": "manual",
            "is_active": True,
        },
    )
    effective = date(2018, 1, 1)
    existing = set(
        UniverseMembership.objects.filter(universe=universe).values_list("ticker", flat=True)
    )
    new = []
    for entry in MACRO_ETF_REGISTRY:
        if entry["ticker"] in existing:
            continue
        new.append(UniverseMembership(
            universe=universe, ticker=entry["ticker"],
            sector=entry["asset_class"],
            effective_from=effective, effective_to=None,
        ))
    if new:
        UniverseMembership.objects.bulk_create(new)


# Risk-parity sleeve universe (P2j). Mix of equity-sector ETFs + bond + gold
# so the inverse-vol weighter has assets with materially different volatility.
RISK_PARITY_SLEEVES: list[tuple[str, str]] = [
    ("XLK", "Technology"),
    ("XLF", "Financials"),
    ("XLE", "Energy"),
    ("XLV", "Health Care"),
    ("XLY", "Consumer Discretionary"),
    ("XLP", "Consumer Staples"),
    ("TLT", "Rates"),
    ("GLD", "Commodity"),
]


def seed_risk_parity_universe(sender=None, **kwargs):
    from .models import Universe, UniverseMembership
    universe, _ = Universe.objects.get_or_create(
        name="risk_parity_sleeves",
        defaults={
            "description": "Curated multi-asset sleeves (sector ETFs + bond proxy + gold) for risk-parity strategies.",
            "source": "manual",
            "is_active": True,
        },
    )
    effective = date(2018, 1, 1)
    existing = set(
        UniverseMembership.objects.filter(universe=universe).values_list("ticker", flat=True)
    )
    new = []
    for ticker, group in RISK_PARITY_SLEEVES:
        if ticker in existing:
            continue
        new.append(UniverseMembership(
            universe=universe, ticker=ticker, sector=group,
            effective_from=effective, effective_to=None,
        ))
    if new:
        UniverseMembership.objects.bulk_create(new)


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
    seed_sector_etf_universe(sender=sender)
    seed_macro_etf_universe(sender=sender)
    seed_risk_parity_universe(sender=sender)
