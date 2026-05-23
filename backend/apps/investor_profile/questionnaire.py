"""Versioned questionnaire schema + validator.

The schema is served verbatim from ``GET /api/profile/questionnaire/`` so the
frontend form and the server-side validator can never drift. Bump
``SCHEMA_VERSION`` whenever ids, option sets, or required flags change; older
responses keep rendering against their stored version.
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = 1
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# id, type, options, required, max_len (text), label, max_select (multi)
SCHEMA: dict = {
    "schema_version": SCHEMA_VERSION,
    "sections": [
        {
            "id": "about",
            "title": "About you",
            "questions": [
                {
                    "id": "age_band",
                    "label": "Your age range",
                    "type": "single",
                    "required": True,
                    "options": [
                        "Under 25",
                        "25–34",
                        "35–44",
                        "45–54",
                        "55–64",
                        "65 or older",
                    ],
                },
                {
                    "id": "experience",
                    "label": "How would you describe your investing experience?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "New (<1 yr)",
                        "Some (1–5 yrs)",
                        "Experienced (5–15 yrs)",
                        "Veteran (15+ yrs)",
                    ],
                },
            ],
        },
        {
            "id": "goals",
            "title": "Goals & horizon",
            "questions": [
                {
                    "id": "primary_goal",
                    "label": "Your primary goal for this money?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "Preserve capital",
                        "Generate income",
                        "Balanced growth",
                        "Aggressive growth",
                        "Learning & experimentation",
                    ],
                },
                {
                    "id": "time_horizon",
                    "label": "When do you expect to need most of this money?",
                    "type": "single",
                    "required": True,
                    "options": ["<1 yr", "1–3 yrs", "3–7 yrs", "7–15 yrs", "15+ yrs"],
                },
                {
                    "id": "asset_share",
                    "label": "What share of your total investable savings is this?",
                    "type": "single",
                    "required": False,
                    "options": [
                        "<10%",
                        "10–40%",
                        "40–75%",
                        ">75%",
                        "Prefer not to say",
                    ],
                },
            ],
        },
        {
            "id": "risk",
            "title": "Risk",
            "questions": [
                {
                    "id": "risk_self_rating",
                    "label": "How would you rate your appetite for risk?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "Very conservative",
                        "Conservative",
                        "Moderate",
                        "Aggressive",
                        "Very aggressive",
                    ],
                },
                {
                    "id": "drawdown_reaction",
                    "label": "Your portfolio falls 25% in a month. What do you do?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "Sell most",
                        "Sell some",
                        "Hold and wait",
                        "Buy a little more",
                        "Buy aggressively",
                    ],
                },
                {
                    "id": "max_annual_loss",
                    "label": "Largest one-year loss you could tolerate without losing sleep",
                    "type": "single",
                    "required": True,
                    "options": [
                        "None",
                        "Up to 10%",
                        "Up to 25%",
                        "Up to 40%",
                        "More than 40%",
                    ],
                },
            ],
        },
        {
            "id": "temperament",
            "title": "Temperament & behavior",
            "questions": [
                {
                    "id": "patience",
                    "label": "A position is underwater but the thesis holds. How long do you hold?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "A few weeks",
                        "A few months",
                        "1–2 years",
                        "As long as the thesis holds",
                    ],
                },
                {
                    "id": "trade_frequency",
                    "label": "How often do you typically trade?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "Rarely (buy & hold)",
                        "A few times a year",
                        "Monthly",
                        "Weekly",
                        "Daily",
                    ],
                },
                {
                    "id": "decision_style",
                    "label": "What drives your decisions most?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "Instinct & gut",
                        "Numbers & data",
                        "News & narratives",
                        "A balanced mix",
                    ],
                },
                {
                    "id": "concentration",
                    "label": "Which is closer to your style?",
                    "type": "single",
                    "required": True,
                    "options": [
                        "A few high-conviction names",
                        "Broad diversification",
                        "Somewhere in between",
                    ],
                },
            ],
        },
        {
            "id": "preferences",
            "title": "Preferences",
            "questions": [
                {
                    "id": "favorite_tickers",
                    "label": "Tickers you currently hold or are watching",
                    "type": "tickers",
                    "required": False,
                    "max_select": 15,
                    "hint": "Up to 15. These will auto-add to your watchlist.",
                },
                {
                    "id": "preferred_sectors",
                    "label": "Sectors or themes you're drawn to",
                    "type": "multi",
                    "required": False,
                    "options": [
                        "Technology",
                        "Healthcare",
                        "Financials",
                        "Energy",
                        "Consumer",
                        "Industrials",
                        "Real estate",
                        "Dividends & income",
                        "Disruptive growth",
                        "ESG",
                        "No preference",
                    ],
                },
                {
                    "id": "avoid",
                    "label": "Anything you prefer to avoid",
                    "type": "multi",
                    "required": False,
                    "options": [
                        "Tobacco",
                        "Weapons",
                        "Fossil fuels",
                        "Highly speculative names",
                        "Leverage / leveraged ETFs",
                        "No restrictions",
                    ],
                },
                {
                    "id": "avoid_notes",
                    "label": "…anything else to avoid",
                    "type": "text",
                    "required": False,
                    "max_len": 200,
                },
                {
                    "id": "free_notes",
                    "label": "Anything else the AI should know about you as an investor?",
                    "type": "text",
                    "required": False,
                    "max_len": 500,
                },
            ],
        },
    ],
}


class QuestionnaireValidationError(ValueError):
    pass


def _flat_questions(schema: dict = SCHEMA) -> list[dict]:
    out: list[dict] = []
    for section in schema["sections"]:
        out.extend(section["questions"])
    return out


def validate_answers(answers: Any, schema_version: int = SCHEMA_VERSION) -> dict:
    """Validate ``answers`` against ``SCHEMA``. Returns normalized answers.

    Raises ``QuestionnaireValidationError`` on first failure (string-cheap so
    the message can be surfaced directly).
    """
    if schema_version != SCHEMA_VERSION:
        raise QuestionnaireValidationError(
            f"Unsupported schema_version {schema_version}; "
            f"current is {SCHEMA_VERSION}."
        )
    if not isinstance(answers, dict):
        raise QuestionnaireValidationError("answers must be an object")

    normalized: dict[str, Any] = {}
    questions = _flat_questions()
    by_id = {q["id"]: q for q in questions}

    for q in questions:
        qid = q["id"]
        present = qid in answers
        value = answers.get(qid)
        if not present or value in (None, "", [], {}):
            if q.get("required"):
                raise QuestionnaireValidationError(
                    f"Missing required answer: {qid}"
                )
            continue
        kind = q["type"]

        if kind == "single":
            if not isinstance(value, str) or value not in q["options"]:
                raise QuestionnaireValidationError(
                    f"Invalid value for {qid}: must be one of the listed options."
                )
            normalized[qid] = value

        elif kind == "multi":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise QuestionnaireValidationError(
                    f"Invalid value for {qid}: must be a list of strings."
                )
            opts = set(q["options"])
            for v in value:
                if v not in opts:
                    raise QuestionnaireValidationError(
                        f"Invalid option for {qid}: {v!r}"
                    )
            normalized[qid] = list(value)

        elif kind == "text":
            if not isinstance(value, str):
                raise QuestionnaireValidationError(
                    f"Invalid value for {qid}: must be a string."
                )
            max_len = int(q.get("max_len", 500))
            v = value.strip()
            if len(v) > max_len:
                raise QuestionnaireValidationError(
                    f"{qid} too long (max {max_len} chars)."
                )
            normalized[qid] = v

        elif kind == "tickers":
            if not isinstance(value, list):
                raise QuestionnaireValidationError(
                    f"Invalid value for {qid}: must be a list."
                )
            max_n = int(q.get("max_select", 15))
            cleaned: list[str] = []
            seen: set[str] = set()
            for raw in value:
                if not isinstance(raw, str):
                    raise QuestionnaireValidationError(
                        f"Invalid ticker for {qid}: not a string."
                    )
                t = raw.strip().upper()
                if not t:
                    continue
                if not TICKER_RE.match(t):
                    raise QuestionnaireValidationError(
                        f"Invalid ticker symbol: {raw!r}"
                    )
                if t in seen:
                    continue
                seen.add(t)
                cleaned.append(t)
                if len(cleaned) > max_n:
                    raise QuestionnaireValidationError(
                        f"Too many tickers for {qid} (max {max_n})."
                    )
            normalized[qid] = cleaned

        else:
            raise QuestionnaireValidationError(f"Unknown question type for {qid}")

    # Drop any unknown ids silently — versioning makes this safe.
    for k in answers.keys():
        if k not in by_id:
            continue
    return normalized
