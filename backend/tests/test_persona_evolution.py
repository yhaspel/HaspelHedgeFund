"""Persona-evolution tests (P3-D WS-E + §15).

Mix of pure unit tests (no DB) and a small set of architectural guards. The
live engine cycle is exercised end-to-end via Chrome MCP (see closeout
notes); these tests cover the contracts that must not regress.
"""
from __future__ import annotations

import datetime as dt
import importlib
import pkgutil

import pytest

from hedgefund_agents.persona_evolution_block import (
    EVOLUTION_FRAMING,
    format_evolution_block,
)

# ---------- format_evolution_block (§15 unit) -------------------------------


def test_format_block_empty_when_no_revision():
    assert format_evolution_block(None) == ""
    assert format_evolution_block("") == ""
    assert format_evolution_block("   \n  ") == ""


def test_format_block_includes_delimiters_and_framing():
    block = format_evolution_block("Buffett bought OXY in Q1 2026.")
    assert EVOLUTION_FRAMING in block
    assert "<<<PERSONA EVOLUTION>>>" in block
    assert "<<<END PERSONA EVOLUTION>>>" in block
    assert "Buffett bought OXY" in block


# ---------- framing distinction (§15 guard) ---------------------------------


def test_evolution_framing_permits_signal_but_not_philosophy():
    """The evolution framing PERMITS movement of the bull/bear signal — that
    is the whole point of the feature — but bounds it WITHIN the core
    philosophy. Regressing to the investor-profile wording (which forbids
    signal movement) would silently neuter the feature."""
    framing = EVOLUTION_FRAMING.lower()
    assert "inform your conviction" in framing
    assert "bullish/bearish signal" in framing
    # The bound: refines the stance WITHIN the core philosophy.
    assert "within your core philosophy" in framing
    assert "never overrides" in framing or "never override" in framing
    # And must NOT have inherited the profile's "do NOT change your signal".
    assert "do not change your bullish" not in framing
    assert "do not change your bearish" not in framing


# ---------- cadence gating (§15 unit) ---------------------------------------


@pytest.mark.django_db
def test_is_cycle_due_matrix():
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from apps.persona_evolution.engine import is_cycle_due
    from apps.persona_evolution.models import (
        PersonaEvolutionProfile,
        PersonaEvolutionSettings,
    )

    User = get_user_model()
    u = User.objects.create_user(email="cadence-test@haspel.test", password="x")
    s = PersonaEvolutionSettings.objects.create(user=u)
    p = PersonaEvolutionProfile.objects.get(persona_name="buffett")

    now = timezone.now()

    # disabled → never due
    s.enabled = False
    assert not is_cycle_due(p, s, now=now)
    s.enabled = True

    # off → never due
    s.cadence = PersonaEvolutionSettings.OFF
    assert not is_cycle_due(p, s, now=now)

    # never-run + any cadence → due
    s.cadence = PersonaEvolutionSettings.WEEKLY
    p.last_cycle_at = None
    assert is_cycle_due(p, s, now=now)

    # just-ran + weekly → not due
    p.last_cycle_at = now - dt.timedelta(days=2)
    assert not is_cycle_due(p, s, now=now)

    # week-old + weekly → due
    p.last_cycle_at = now - dt.timedelta(days=8)
    assert is_cycle_due(p, s, now=now)

    # non-evolvable → never due
    p_munger = PersonaEvolutionProfile.objects.get(persona_name="munger")
    p_munger.last_cycle_at = None
    assert not is_cycle_due(p_munger, s, now=now)


# ---------- composite_markdown contract (§15 unit) --------------------------


@pytest.mark.django_db
def test_composite_markdown_omits_empty_sections():
    from apps.persona_evolution.models import (
        PersonaEvolutionProfile,
        PersonaEvolutionRevision,
    )

    p = PersonaEvolutionProfile.objects.get(persona_name="buffett")
    today = dt.date(2026, 5, 25)

    rev = PersonaEvolutionRevision.objects.create(
        profile=p, seq=1, as_of_date=today,
        market_stance_md="Bought OXY (2026-Q1).",
        general_notes_md="",
    )
    md = rev.composite_markdown()
    assert "RECENT MARKET STANCE & MOVES (as of 2026-05-25)" in md
    assert "GENERAL NOTES" not in md
    assert "Bought OXY" in md

    rev.market_stance_md = ""
    rev.general_notes_md = "Annual letter published."
    rev.save()
    md = rev.composite_markdown()
    assert "RECENT MARKET STANCE" not in md
    assert "GENERAL NOTES" in md

    rev.market_stance_md = ""
    rev.general_notes_md = ""
    rev.save()
    assert rev.composite_markdown() == ""


# ---------- resolve_evolution_for_run picks latest <= as_of_date ------------


@pytest.mark.django_db
def test_resolve_evolution_for_run_pit_correctness():
    from apps.persona_evolution.engine import resolve_evolution_for_run
    from apps.persona_evolution.models import (
        PersonaEvolutionProfile,
        PersonaEvolutionRevision,
    )

    p = PersonaEvolutionProfile.objects.get(persona_name="buffett")
    PersonaEvolutionRevision.objects.create(
        profile=p, seq=1, as_of_date=dt.date(2026, 4, 1),
        market_stance_md="April stance.", general_notes_md="",
    )
    PersonaEvolutionRevision.objects.create(
        profile=p, seq=2, as_of_date=dt.date(2026, 5, 1),
        market_stance_md="May stance.", general_notes_md="",
    )

    # exactly at as_of: pick the same-day revision
    out = resolve_evolution_for_run(dt.date(2026, 5, 1))
    assert "May stance." in out["buffett"]

    # past-date strictly between rev1 and rev2: pick rev1
    out = resolve_evolution_for_run(dt.date(2026, 4, 30))
    assert "April stance." in out["buffett"]
    assert "May stance." not in out["buffett"]

    # before any revision: empty
    out = resolve_evolution_for_run(dt.date(2026, 3, 31))
    assert "buffett" not in out

    # Graham/Munger never appear (is_evolvable=False)
    assert "graham" not in out
    assert "munger" not in out


# ---------- spec_hash stability (§15 must-have guard) -----------------------


@pytest.mark.django_db
def test_core_persona_spec_hash_stable_after_revision_writes():
    """A full cycle's worth of revision writes must NOT change any persona's
    AgentSpec.spec_hash — that is the structural guarantee of decision 6.
    """
    import hedgefund_agents.personas  # noqa: F401 — register specs
    from apps.persona_evolution.models import (
        PersonaEvolutionProfile,
        PersonaEvolutionRevision,
    )
    from hedgefund_agents.personas import ALL_PERSONAS
    from hedgefund_agents.versioning import AGENT_VERSIONS

    before = {n: AGENT_VERSIONS[n].spec_hash for n in ALL_PERSONAS}

    # Write a revision per evolvable persona — simulating what a cycle does.
    for p in PersonaEvolutionProfile.objects.filter(is_evolvable=True):
        PersonaEvolutionRevision.objects.create(
            profile=p, seq=99, as_of_date=dt.date(2026, 5, 25),
            market_stance_md=f"{p.display_name} did things in Q1.",
            general_notes_md="Some general note.",
        )

    after = {n: AGENT_VERSIONS[n].spec_hash for n in ALL_PERSONAS}
    assert before == after, (
        "spec_hash changed after writing revisions — decision 6 (core persona "
        "is never modified or extended) is violated."
    )


# ---------- backtest boundary (§15 must-have guard) -------------------------


def test_apps_persona_evolution_not_imported_by_backtests():
    """No module reachable under ``apps.backtests`` may import
    ``apps.persona_evolution``. Injection happens ONLY inside
    ``apps.runs.tasks.execute_run`` (§6.3)."""
    import apps.backtests as backtests_pkg

    forbidden = "apps.persona_evolution"
    bad: list[str] = []
    for info in pkgutil.walk_packages(backtests_pkg.__path__, prefix="apps.backtests."):
        if ".tests." in info.name or info.name.endswith(".tests"):
            continue
        module = importlib.import_module(info.name)
        src = getattr(module, "__file__", None)
        if not src:
            continue
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        if forbidden in content:
            bad.append(f"{info.name} imports {forbidden}")
    assert not bad, "\n".join(bad)


# ---------- backtest carries no evolution delimiter -------------------------


@pytest.mark.django_db
def test_backtest_initial_state_omits_persona_evolution():
    """Defence-in-depth: the backtest engine builds initial_state without a
    ``persona_evolution`` key, so even if the boundary import test ever
    regresses, the persona node's ``.get("persona_evolution", {})`` falls
    through to an empty dict → empty block → core persona only.

    We assert this by re-reading the engine's source and confirming the
    string ``persona_evolution`` is absent from the prime path (other than
    in comments — which we also expect to be absent for now).
    """
    import inspect

    from apps.backtests import engine as bt_engine

    src = inspect.getsource(bt_engine)
    assert "persona_evolution" not in src, (
        "apps.backtests.engine mentions 'persona_evolution' — the live-only "
        "boundary may have regressed. See §6.3."
    )


# ---------- bloat truncation rounds to a sentence boundary ------------------


def test_truncate_at_sentence_boundary():
    from apps.persona_evolution.engine import _truncate_at_sentence

    text = (
        "First sentence is short. "
        "Second sentence is also short. "
        "Third sentence keeps going and going and going and going."
    )
    cut, was = _truncate_at_sentence(text, budget=60)
    assert was is True
    # Should end on a sentence boundary, not mid-word.
    assert cut.endswith(".") or cut.endswith(".\n")
    assert len(cut) <= 60
