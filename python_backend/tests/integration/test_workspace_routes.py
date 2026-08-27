"""API route tests for the GeoPilot workspace (CPT interpret + status).

Integration tests: they exercise the FastAPI app end-to-end via ASGI. Auth is
supplied by overriding ``get_current_user`` (no real JWT/DB), the feature flag
is toggled with monkeypatch, and ``interpret_sounding`` (the LLM call) is mocked
so nothing hits Ollama. The deterministic parse -> calculator -> layer_summary
chain runs for real.
"""

from datetime import datetime

import pytest

from app.core import config
from app.dependencies.auth import get_current_user
from app.main import app
from app.workspace import routes as workspace_routes
from app.workspace.data import SAMPLE_CPT_FILE
from app.workspace.interpretation.ai_interpret import InterpretationResult
from models import User

pytestmark = pytest.mark.integration


def _fake_user() -> User:
    return User(
        id="507f1f77bcf86cd799439011",
        email="tester@example.com",
        hashed_password="x",
        full_name="Tester",
        created_at=datetime.now(),
        role="user",
    )


@pytest.fixture
def authed():
    """Override the auth dependency so requests are authenticated."""
    app.dependency_overrides[get_current_user] = _fake_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def enable_workspace(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)


@pytest.fixture
def mock_interpret(monkeypatch):
    """Replace the LLM interpretation with a canned, grounded result."""

    async def _fake(results, **kwargs):
        return InterpretationResult(
            narrative="Silt over sand over clay. AI draft for review.",
            per_layer_notes=[{"layer": 1}, {"layer": 2}, {"layer": 3}],
            flagged_concerns=["a concern"],
            model="test-model",
            is_ai_draft=True,
        )

    monkeypatch.setattr(workspace_routes, "interpret_sounding", _fake)


def _upload_files():
    with open(SAMPLE_CPT_FILE, "rb") as fh:
        return {"file": ("sample_sounding.CPT", fh.read(), "application/octet-stream")}


# --- Auth required ----------------------------------------------------------

@pytest.mark.asyncio
async def test_status_requires_auth(async_client):
    resp = await async_client.get("/api/workspace/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_interpret_requires_auth(async_client, enable_workspace):
    # Flag ON so the 401 is unambiguously about auth, not the gate.
    resp = await async_client.post("/api/workspace/cpt/interpret", files=_upload_files())
    assert resp.status_code == 401


# --- Feature flag gate ------------------------------------------------------

@pytest.mark.asyncio
async def test_status_reports_flag(async_client, authed, monkeypatch):
    # This test pins the PRE-instrument-parsers payload ({"enabled": ...}
    # only). With INSTRUMENT_PARSERS_ENABLED on (the dev/prod .env) the status
    # route legitimately adds instrument_parsers/instrument_extensions (see
    # test_workspace_datasets for that contract), so pin that flag OFF here.
    monkeypatch.setattr(config, "INSTRUMENT_PARSERS_ENABLED", False)
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)
    assert (await async_client.get("/api/workspace/status")).json() == {"enabled": True}

    monkeypatch.setattr(config, "WORKSPACE_ENABLED", False)
    assert (await async_client.get("/api/workspace/status")).json() == {"enabled": False}


@pytest.mark.asyncio
async def test_interpret_disabled_returns_404(async_client, authed, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", False)
    resp = await async_client.post("/api/workspace/cpt/interpret", files=_upload_files())
    assert resp.status_code == 404


# --- Valid upload -> expected JSON shape ------------------------------------

@pytest.mark.asyncio
async def test_interpret_valid_upload_returns_expected_shape(
    async_client, authed, enable_workspace, mock_interpret
):
    resp = await async_client.post(
        "/api/workspace/cpt/interpret",
        files=_upload_files(),
        data={"groundwater_level": "1.5"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level shape.
    assert set(body.keys()) == {"layers", "interpretation", "header"}

    # Layers: the deterministic 3-layer stratigraphy.
    assert len(body["layers"]) == 3
    l0 = body["layers"][0]
    for key in ("depth_from", "depth_to", "soil_type", "qc_mean", "ic_mean"):
        assert key in l0
    assert body["layers"][1]["soil_type"].startswith("Sand")

    # Interpretation block.
    interp = body["interpretation"]
    assert interp["is_ai_draft"] is True
    assert interp["narrative"]
    assert isinstance(interp["per_layer_notes"], list)
    assert isinstance(interp["flagged_concerns"], list)

    # Header: area ratio provenance comes from the file's MA field, not hardcoded.
    assert body["header"]["area_ratio_source"] == "MA"
    assert body["header"]["area_ratio"] == pytest.approx(0.80)
    assert body["header"]["n_layers"] == 3


@pytest.mark.asyncio
async def test_interpret_rejects_empty_file(
    async_client, authed, enable_workspace, mock_interpret
):
    resp = await async_client.post(
        "/api/workspace/cpt/interpret",
        files={"file": ("empty.CPT", b"", "application/octet-stream")},
    )
    assert resp.status_code == 400
