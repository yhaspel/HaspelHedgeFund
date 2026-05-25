"""Seed the eight `PersonaEvolutionProfile` rows (P3-D WS-A, decision 4)."""
from django.db import migrations


SEED = [
    # (persona_name, display_name, firm_name, is_evolvable, lifecycle_note, aliases)
    (
        "buffett", "Warren Buffett", "Berkshire Hathaway", True, "",
        ["Berkshire", "BRK", "BRK.A", "BRK.B"],
    ),
    (
        "wood", "Cathie Wood", "ARK Invest", True, "",
        ["ARK", "ARKK", "ARKG", "ARKQ", "ARKW"],
    ),
    (
        "druckenmiller", "Stanley Druckenmiller", "Duquesne Family Office", True, "",
        ["Duquesne", "Druckenmiller Foundation"],
    ),
    (
        "burry", "Michael Burry", "Scion Asset Management", True, "",
        ["Scion", "Scion Asset"],
    ),
    (
        "damodaran",
        "Aswath Damodaran",
        "NYU Stern",
        True,
        "academic/blogger — expect thin moves, richer notes",
        ["NYU Stern", "Damodaran Online", "Musings on Markets"],
    ),
    (
        "lynch",
        "Peter Lynch",
        "Fidelity",
        True,
        "retired from active management — expect thin moves",
        ["Fidelity Magellan", "Magellan Fund"],
    ),
    (
        "graham", "Benjamin Graham", "", False, "deceased 1976 — philosophy is canon",
        [],
    ),
    (
        "munger", "Charlie Munger", "", False, "deceased 2023 — philosophy is canon",
        [],
    ),
]


def seed_profiles(apps, schema_editor):
    PersonaEvolutionProfile = apps.get_model(
        "persona_evolution", "PersonaEvolutionProfile"
    )
    for name, display, firm, evolvable, note, aliases in SEED:
        PersonaEvolutionProfile.objects.update_or_create(
            persona_name=name,
            defaults={
                "display_name": display,
                "firm_name": firm,
                "is_evolvable": evolvable,
                "lifecycle_note": note,
                "search_aliases": aliases,
            },
        )


def unseed_profiles(apps, schema_editor):
    PersonaEvolutionProfile = apps.get_model(
        "persona_evolution", "PersonaEvolutionProfile"
    )
    PersonaEvolutionProfile.objects.filter(
        persona_name__in=[row[0] for row in SEED]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("persona_evolution", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_profiles, reverse_code=unseed_profiles),
    ]
