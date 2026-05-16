"""Test bootstrap: load .env, register VCR config that scrubs secrets."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load project .env so VCR-recording test runs see real API keys.
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


_SCRUB_HEADERS = ("authorization", "x-api-key", "cookie", "set-cookie")
_SCRUB_QUERY = ("apikey", "api_key")


def _before_record_request(request):
    # Scrub apikey from query string before recording.
    if request.query:
        request.uri = request.uri  # placeholder; vcrpy handles via filter_query_parameters
    return request


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "filter_headers": list(_SCRUB_HEADERS),
        "filter_query_parameters": [(p, "REDACTED") for p in _SCRUB_QUERY],
        "filter_post_data_parameters": [("api_key", "REDACTED")],
        "decode_compressed_response": True,
        "record_mode": os.environ.get("VCR_RECORD_MODE", "once"),
    }
