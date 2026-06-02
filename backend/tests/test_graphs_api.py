"""P4c graph editor API: CRUD, versions, validate, from-template, scoping."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.graphs.models import AgentGraph
from apps.graphs.templates import create_canonical_council_version

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return User.objects.create_user(email="g@example.com", password="x" * 12)


@pytest.fixture
def other_user():
    return User.objects.create_user(email="h@example.com", password="x" * 12)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _canonical_payload(notes="v1"):
    j, tail = create_canonical_council_version()
    return {"nodes": j["nodes"], "edges": j["edges"], "tail_models": tail, "notes": notes}


# ---- registry ----------------------------------------------------------

def test_registry_endpoint(client):
    r = client.get("/api/graphs/registry/")
    assert r.status_code == 200
    body = r.json()
    assert len(body["analytical"]) == 6
    assert len(body["personas"]) == 8
    assert [t["agent_name"] for t in body["tail"]] == ["risk_manager", "portfolio_manager", "cio"]
    pm = next(t for t in body["tail"] if t["agent_name"] == "portfolio_manager")
    assert pm["model_selectable"] is False
    assert "macro" in body["notes"]
    # Bulk tier switcher: each tier has a curated model menu + a default model.
    tiers = {t["name"]: t for t in body["tiers"]}
    assert {"dev", "frugal", "hybrid", "research", "quality"} <= set(tiers)
    frugal = tiers["frugal"]
    assert frugal["default_model"] == "openrouter:qwen/qwen3.6-27b"
    assert frugal["default_model"] in frugal["models"]
    assert "openrouter:meta-llama/llama-3.3-70b-instruct" in frugal["models"]
    assert tiers["dev"]["default_model"] == "openrouter:openai/gpt-oss-120b:free"


# ---- list / templates --------------------------------------------------

def test_list_shows_templates(client):
    r = client.get("/api/graphs/")
    assert r.status_code == 200
    names = {g["name"] for g in r.json()}
    assert {"Council classic", "Sector rotation", "Single persona (Buffett)", "Value only"} <= names
    # templates are not owned
    council = next(g for g in r.json() if g["name"] == "Council classic")
    assert council["is_template"] is True
    assert council["owned"] is False
    assert council["latest_version"]["validation_status"] == "valid"


# ---- create + uniqueness ----------------------------------------------

def test_create_graph_and_name_conflict(client):
    r = client.post("/api/graphs/", {"name": "My Graph", "description": "d"}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["owned"] is True
    # same name → conflict
    r2 = client.post("/api/graphs/", {"name": "My Graph"}, format="json")
    assert r2.status_code == 409


# ---- save version (valid + invalid) -----------------------------------

def test_save_valid_version_increments(client):
    g = client.post("/api/graphs/", {"name": "G"}, format="json").json()
    gid = g["id"]
    r = client.post(f"/api/graphs/{gid}/versions/", _canonical_payload(), format="json")
    assert r.status_code == 201, r.content
    assert r.json()["version"] == 1
    assert r.json()["validation_status"] == "valid"
    r2 = client.post(f"/api/graphs/{gid}/versions/", _canonical_payload("v2"), format="json")
    assert r2.json()["version"] == 2


def test_save_invalid_version_rejected(client):
    g = client.post("/api/graphs/", {"name": "Bad"}, format="json").json()
    gid = g["id"]
    # no persona → Error
    payload = {"nodes": [{"id": "fundamentals", "type": "fundamentals"}],
               "edges": [{"from": "entry", "to": "fundamentals"},
                         {"from": "fundamentals", "to": "analytical_join"}],
               "tail_models": {}}
    r = client.post(f"/api/graphs/{gid}/versions/", payload, format="json")
    assert r.status_code == 400
    rules = {e["rule"] for e in r.json()["errors"]}
    assert "no_persona" in rules


def test_save_version_fills_default_models(client):
    g = client.post("/api/graphs/", {"name": "Defaults"}, format="json").json()
    gid = g["id"]
    payload = _canonical_payload()
    for n in payload["nodes"]:
        n.pop("model_id", None)  # strip all models
    payload["tail_models"] = {}
    r = client.post(f"/api/graphs/{gid}/versions/", payload, format="json")
    assert r.status_code == 201, r.content
    saved = r.json()
    assert all(n["model_id"] for n in saved["nodes"])  # server filled them
    assert saved["tail_models"]["risk_manager"] and saved["tail_models"]["cio"]


# ---- validate (no save) -----------------------------------------------

def test_validate_endpoint_no_save(client):
    before = AgentGraph.objects.count()
    r = client.post("/api/graphs/validate/", _canonical_payload(), format="json")
    assert r.status_code == 200
    assert r.json()["is_valid"] is True
    assert AgentGraph.objects.count() == before  # nothing persisted


# ---- from-template -----------------------------------------------------

def test_from_template_clones_independently(client):
    council = AgentGraph.objects.get(name="Council classic", is_template=True)
    r = client.post(
        f"/api/graphs/from-template/{council.id}/", {"name": "My Council"}, format="json"
    )
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["owned"] is True
    assert body["is_template"] is False
    assert body["latest_version"]["validation_status"] == "valid"
    # name required
    r2 = client.post(f"/api/graphs/from-template/{council.id}/", {}, format="json")
    assert r2.status_code == 400


# ---- read-only templates ----------------------------------------------

def test_template_is_read_only(client):
    council = AgentGraph.objects.get(name="Council classic", is_template=True)
    patch_resp = client.patch(f"/api/graphs/{council.id}/", {"name": "x"}, format="json")
    assert patch_resp.status_code == 403
    assert client.delete(f"/api/graphs/{council.id}/").status_code == 403
    assert client.post(f"/api/graphs/{council.id}/versions/", _canonical_payload(),
                       format="json").status_code == 403


# ---- scoping -----------------------------------------------------------

def test_user_cannot_see_others_graph(client, other_user):
    g = AgentGraph.objects.create(user=other_user, name="Theirs")
    assert client.get(f"/api/graphs/{g.id}/").status_code == 404
    # not in the list either
    names = {x["name"] for x in client.get("/api/graphs/").json()}
    assert "Theirs" not in names


def test_archive_hides_graph(client):
    g = client.post("/api/graphs/", {"name": "ToArchive"}, format="json").json()
    assert client.delete(f"/api/graphs/{g['id']}/").status_code == 204
    names = {x["name"] for x in client.get("/api/graphs/").json()}
    assert "ToArchive" not in names


def test_version_detail_loads(client):
    g = client.post("/api/graphs/", {"name": "Loadable"}, format="json").json()
    gid = g["id"]
    client.post(f"/api/graphs/{gid}/versions/", _canonical_payload(), format="json")
    r = client.get(f"/api/graphs/{gid}/versions/1/")
    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 14
