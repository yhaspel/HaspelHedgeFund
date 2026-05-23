"""Point-in-time architecture guard for the market screener (P3 prereq 3).

The market screener serves *today* data only. If it were ever imported
from a backtest or any point-in-time agent path, the no-look-ahead
guarantee would silently break. This test asserts the boundary by grepping
the source tree.
"""
from __future__ import annotations

import pathlib
import re

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

PIT_PATH_PATTERNS = (
    re.compile(r"backend/apps/backtests/"),
    re.compile(r"backend/hedgefund_agents/(?!.*/__pycache__).*\.py$"),
)

FORBIDDEN_IMPORT = re.compile(r"^\s*(?:from|import)\s+apps\.screener\b", re.MULTILINE)


def _iter_pit_paths():
    for p in (BACKEND_ROOT / "apps" / "backtests").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p
    for p in (BACKEND_ROOT / "hedgefund_agents").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_screener_is_not_imported_from_pit_paths() -> None:
    offenders: list[str] = []
    for path in _iter_pit_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if FORBIDDEN_IMPORT.search(text):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))
    assert not offenders, (
        "apps.screener must not be imported from any backtest / "
        f"point-in-time path. Offenders: {offenders}"
    )
