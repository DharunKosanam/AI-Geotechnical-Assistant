"""Audit F-01 (2026-08-26): the two write paths that took a threadId from the
request without checking it against the caller.

  * POST /api/upload with threadId  -> the thread must be the caller's own
    or (CHAT_SHARING_ENABLED) one they joined; 404 for an unregistered id,
    403 otherwise, and NOTHING is persisted or scheduled on a rejection.
  * POST /chat/formats/stream       -> the same membership gate as the chat
    turn, raised before the stream starts (a real 403 status).

Driven through the real ASGI app with auth overridden (X-Test-User header),
the limiter disabled, and every collection / ingestion / generation faked.
No Mongo, no Redis, no LLM.
"""
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from bson import ObjectId
from fastapi import Header, HTTPException

from app.core import config
from app.core.rate_limit import limiter
from app.dependencies.auth import get_current_user
from app.main import app
from app.routers import chat as chat_mod
from app.routers import files as files_mod
from app.services import rag_service
from models import User
from tests.unit.test_chat_sharing import FakeColl


class _Files(FakeColl):
    """FakeColl + a motor-shaped insert_one result (the upload route reads
    inserted_id for the parent doc)."""

    async def insert_one(self, doc):
        d = dict(doc)
        d.setdefault("_id", ObjectId())
        self.docs.append(d)
        return SimpleNamespace(inserted_id=d["_id"])

pytestmark = pytest.mark.unit

T0 = datetime(2026, 8, 26, 10, 0, 0)
SHARED = {"threadId": "th-shared", "userId": "uA", "name": "GRS wall", "isGroup": True,
          "members": ["uA", "uB"], "createdAt": T0, "updatedAt": T0}
PRIVATE = {"threadId": "th-private", "userId": "uA", "name": "private", "isGroup": False,
           "members": ["uA"], "createdAt": T0, "updatedAt": T0}


async def _fake_user(x_test_user: str = Header(default=None)) -> User:
    if not x_test_user:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return User(id=x_test_user, email=f"{x_test_user}@uvic.ca", hashed_password="x")


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(config, "CHAT_SHARING_ENABLED", True)
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    monkeypatch.setattr(limiter, "enabled", False)
    convs = FakeColl([SHARED, PRIVATE])
    files = _Files([])
    monkeypatch.setattr(files_mod, "conversations_collection", convs)
    monkeypatch.setattr(files_mod, "files_collection", files)
    monkeypatch.setattr(chat_mod, "conversations_collection", convs)
    monkeypatch.setattr(rag_service, "_ingest_inflight", 0)

    scheduled = []

    async def fake_ingest(filename, file_content, category, parent_id, user_id, thread_id, *a):
        scheduled.append({"filename": filename, "user_id": user_id, "thread_id": thread_id,
                          "category": category})
        rag_service.ingest_release()

    monkeypatch.setattr(files_mod, "process_file_ingestion", fake_ingest)

    formats_run = []

    async def fake_sse_format_turn(payload, current_user):
        formats_run.append({"threadId": payload.threadId, "user": current_user.id})
        yield "event: start\ndata: {}\n\n"

    monkeypatch.setattr(chat_mod, "_sse_format_turn", fake_sse_format_turn)
    monkeypatch.setattr(chat_mod, "_format_job_active", False)

    app.dependency_overrides[get_current_user] = _fake_user
    try:
        yield {"convs": convs, "files": files, "scheduled": scheduled, "formats": formats_run}
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        chat_mod._format_job_active = False


@pytest.fixture()
async def client(env):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _upload(client, user, thread_id=None):
    data = {"threadId": thread_id} if thread_id is not None else {}
    return client.post(
        "/api/upload",
        files={"file": ("notes.csv", b"a,b\n1,2\n", "text/csv")},
        data=data,
        headers={"X-Test-User": user},
    )


# --- POST /api/upload ---------------------------------------------------------
async def test_non_member_upload_is_403_and_persists_nothing(client, env):
    res = await _upload(client, "uC", "th-shared")
    assert res.status_code == 403, res.text
    assert env["files"].docs == []          # no parent doc
    assert env["scheduled"] == []           # no ingestion scheduled
    assert rag_service.ingest_queue_depth() == 0   # no slot held


async def test_non_member_upload_into_private_thread_is_403(client, env):
    res = await _upload(client, "uC", "th-private")
    assert res.status_code == 403
    assert env["files"].docs == []


async def test_unregistered_thread_upload_is_404(client, env):
    res = await _upload(client, "uA", "th-does-not-exist")
    assert res.status_code == 404
    assert env["files"].docs == []
    assert env["scheduled"] == []


async def test_owner_upload_still_succeeds(client, env):
    res = await _upload(client, "uA", "th-shared")
    assert res.status_code == 200, res.text
    assert [d["userId"] for d in env["files"].docs] == ["uA"]
    assert env["files"].docs[0]["threadId"] == "th-shared"
    assert env["scheduled"] == [{"filename": "notes.csv", "user_id": "uA",
                                 "thread_id": "th-shared", "category": "thread_upload"}]


async def test_member_upload_still_succeeds_flag_on(client, env):
    res = await _upload(client, "uB", "th-shared")
    assert res.status_code == 200, res.text
    assert env["scheduled"][0]["user_id"] == "uB"


async def test_member_upload_is_403_flag_off(client, env, monkeypatch):
    # "Joined" only exists while sharing is on: flag-off a member is just
    # another user, and only the owner may attach documents.
    monkeypatch.setattr(config, "CHAT_SHARING_ENABLED", False)
    assert (await _upload(client, "uB", "th-shared")).status_code == 403
    assert (await _upload(client, "uA", "th-shared")).status_code == 200


async def test_upload_without_thread_id_is_unchanged(client, env):
    res = await _upload(client, "uC")
    assert res.status_code == 200, res.text
    assert env["convs"].queries == []       # no thread lookup at all
    assert env["files"].docs[0]["category"] == "user_upload"


# --- POST /chat/formats/stream -----------------------------------------------
def _formats(client, user, thread_id):
    return client.post("/chat/formats/stream",
                       json={"threadId": thread_id, "format": "study_guide"},
                       headers={"X-Test-User": user})


async def test_non_member_formats_is_403_before_the_stream(client, env):
    res = await _formats(client, "uC", "th-shared")
    assert res.status_code == 403
    assert "not a member" in res.json()["detail"]
    assert env["formats"] == []             # generation never started
    assert chat_mod._format_job_active is False   # slot never taken


async def test_non_member_formats_into_private_thread_is_403(client, env):
    assert (await _formats(client, "uC", "th-private")).status_code == 403


async def test_owner_and_member_formats_still_succeed(client, env):
    for user in ("uA", "uB"):
        res = await _formats(client, user, "th-shared")
        assert res.status_code == 200, (user, res.text)
        assert res.headers["content-type"].startswith("text/event-stream")
        chat_mod._format_job_active = False
    assert [r["user"] for r in env["formats"]] == ["uA", "uB"]


async def test_formats_flag_gate_precedes_membership(client, env, monkeypatch):
    # SOURCE_FORMATS_ENABLED off is still a 404 -- the membership check never
    # runs (route absent semantics unchanged).
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", False)
    assert (await _formats(client, "uC", "th-shared")).status_code == 404
    assert env["convs"].queries == []
