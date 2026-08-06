"""Integration tests for GeoPilot History (runs + threads, per-user isolation).

Uses the autouse in-memory ``workspace_db`` fake (see conftest) so nothing hits
real Atlas. Auth is overridden per-request so we can act as different users and
prove a user can never see another user's runs or threads.
"""

import io
from datetime import datetime
from pathlib import Path

import pytest

from app.core import config
from app.dependencies.auth import get_current_user
from app.main import app
from app.workspace import store
from app.workspace.data import SAMPLE_CPT_FILE
from app.workspace.interpretation.ai_interpret import InterpretationResult
from models import User

pytestmark = pytest.mark.integration


def _user(uid: str) -> User:
    return User(
        id=uid,
        email=f"{uid}@example.com",
        hashed_password="x",
        full_name=uid,
        created_at=datetime.now(),
        role="user",
    )


def _act_as(uid: str):
    app.dependency_overrides[get_current_user] = lambda: _user(uid)


def _sample_bytes() -> bytes:
    return Path(SAMPLE_CPT_FILE).read_bytes()


@pytest.fixture
def enable_workspace(monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", True)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.pop(get_current_user, None)
    store.clear_user("alice")
    store.clear_user("bob")


@pytest.fixture
def mock_interpret(monkeypatch):
    async def _fake(results, **kwargs):
        return InterpretationResult(
            narrative="AI draft for review.",
            per_layer_notes=[],
            flagged_concerns=[],
            model="test-model",
            is_ai_draft=True,
        )

    import app.workspace.calculators.cpt as cpt_mod

    monkeypatch.setattr(cpt_mod, "interpret_sounding", _fake)


async def _run_cpt_as(uid: str, async_client) -> dict:
    _act_as(uid)
    await async_client.post(
        "/api/workspace/documents",
        files={"file": ("sample_sounding.CPT", _sample_bytes(), "text/plain")},
    )
    resp = await async_client.post("/api/workspace/chat", json={"message": "run cpt"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Runs appear in History ------------------------------------------------
@pytest.mark.asyncio
async def test_run_appears_in_history(
    enable_workspace, mock_interpret, async_client
):
    run = await _run_cpt_as("alice", async_client)

    listing = await async_client.get("/api/workspace/history/runs")
    assert listing.status_code == 200
    runs = listing.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["id"] == run["run_id"]
    assert runs[0]["source_filename"] == "sample_sounding.CPT"
    assert runs[0]["summary"]["layer_count"] >= 1

    detail = await async_client.get(f"/api/workspace/history/runs/{run['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["result_object"]["calculator_id"] == "cpt_interpretation"


# --- Thread persistence + reload -------------------------------------------
@pytest.mark.asyncio
async def test_thread_records_and_reloads_messages(
    enable_workspace, mock_interpret, async_client
):
    run = await _run_cpt_as("alice", async_client)
    thread_id = run["thread_id"]
    assert thread_id

    threads = (await async_client.get("/api/workspace/history/threads")).json()["threads"]
    assert len(threads) == 1
    assert threads[0]["id"] == thread_id

    thread = (await async_client.get(f"/api/workspace/history/threads/{thread_id}")).json()
    roles = [(m["role"], m["type"]) for m in thread["messages"]]
    assert ("user", "text") in roles
    assert ("assistant", "result") in roles
    # The recorded result message carries the full payload for re-render.
    result_msg = next(m for m in thread["messages"] if m["type"] == "result")
    assert result_msg["content"]["layers"]


# --- Per-user isolation ----------------------------------------------------
@pytest.mark.asyncio
async def test_users_cannot_see_each_others_history(
    enable_workspace, mock_interpret, async_client
):
    alice_run = await _run_cpt_as("alice", async_client)
    await _run_cpt_as("bob", async_client)

    _act_as("bob")
    bob_runs = (await async_client.get("/api/workspace/history/runs")).json()["runs"]
    assert len(bob_runs) == 1
    assert bob_runs[0]["id"] != alice_run["run_id"]

    # Bob requesting Alice's run id directly -> clean 404, not her data.
    resp = await async_client.get(f"/api/workspace/history/runs/{alice_run['run_id']}")
    assert resp.status_code == 404

    # And Bob cannot export Alice's run either.
    resp = await async_client.get(f"/api/workspace/export/{alice_run['run_id']}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_gated_by_flag(monkeypatch, async_client):
    monkeypatch.setattr(config, "WORKSPACE_ENABLED", False)
    _act_as("alice")
    resp = await async_client.get("/api/workspace/history/runs")
    assert resp.status_code == 404  # WORKSPACE_ENABLED off -> route hidden