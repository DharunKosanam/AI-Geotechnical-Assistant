"""Part 2 (SOURCE_SETS_ENABLED) tests: the sources view and per-source
removal routes.

The corpus is adversarial, extending the cascade-delete test's five-way set:
same filename in the knowledge base, as a plain user_upload, in another
thread, AND owned by another user under the same threadId -- so the
isolation assertions are on survivors, not just on deletion counts.
"""

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from app.core import config
from app.dependencies.auth import get_current_user
from app.main import app
from app.services import rag_service
from models import User
import app.routers.threads as threads_mod

from tests.integration.test_thread_rename import _FakeColl

pytestmark = pytest.mark.integration


def _corpus():
    now = datetime.now()
    return [
        # thread tA, user U: a.pdf ready with one verbatim + one vision chunk
        {"_id": "aP", "userId": "U", "threadId": "tA", "category": "thread_upload",
         "filename": "a.pdf", "chunkCount": 2, "status": "processed"},
        {"_id": "a0", "userId": "U", "threadId": "tA", "category": "thread_upload",
         "filename": "a.pdf", "chunkIndex": 0, "text": "t", "metadata": {}},
        {"_id": "a1", "userId": "U", "threadId": "tA", "category": "thread_upload",
         "filename": "a.pdf", "chunkIndex": 1, "text": "v",
         "metadata": {"visionDerived": True}, "pageStart": 3},
        # b.pdf still processing (fresh -> pending)
        {"_id": "bP", "userId": "U", "threadId": "tA", "category": "thread_upload",
         "filename": "b.pdf", "chunkCount": 0, "status": "processing", "createdAt": now},
        # c.pdf failed
        {"_id": "cP", "userId": "U", "threadId": "tA", "category": "thread_upload",
         "filename": "c.pdf", "chunkCount": 0, "status": "failed", "error": "Unreadable"},
        # d.pdf fully vision-derived AND partially indexed
        {"_id": "dP", "userId": "U", "threadId": "tA", "category": "thread_upload",
         "filename": "d.pdf", "chunkCount": 1, "status": "processed",
         "warning": "2 pages could not be read"},
        {"_id": "d0", "userId": "U", "threadId": "tA", "category": "thread_upload",
         "filename": "d.pdf", "chunkIndex": 0, "text": "v",
         "metadata": {"visionDerived": True}, "pageStart": 1},
        # SURVIVOR SET: same filename everywhere it must not be touched
        {"_id": "xP", "userId": "U", "threadId": "tB", "category": "thread_upload",
         "filename": "a.pdf", "chunkCount": 1, "status": "processed"},
        {"_id": "x0", "userId": "U", "threadId": "tB", "category": "thread_upload",
         "filename": "a.pdf", "chunkIndex": 0, "text": "t", "metadata": {}},
        {"_id": "kb", "category": "knowledge_base", "filename": "a.pdf",
         "chunkIndex": 0, "text": "t", "metadata": {}},
        {"_id": "uu", "userId": "U", "category": "user_upload", "filename": "a.pdf",
         "chunkIndex": 0, "text": "t", "metadata": {}},
        # another user's doc carrying thread A's exact threadId + filename
        {"_id": "fA", "userId": "V", "threadId": "tA", "category": "thread_upload",
         "filename": "a.pdf", "chunkIndex": 0, "text": "t", "metadata": {}},
    ]


@pytest_asyncio.fixture
async def sets_env(monkeypatch):
    fake_user = User(id="U", email="u@example.com", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: fake_user

    files = _FakeColl(_corpus())
    conversations = _FakeColl([
        {"userId": "U", "threadId": "tA", "name": "Set A", "isGroup": False},
        {"userId": "U", "threadId": "tB", "name": "Set B", "isGroup": False},
    ])
    monkeypatch.setattr(threads_mod, "files_collection", files)
    monkeypatch.setattr(threads_mod, "conversations_collection", conversations)
    monkeypatch.setattr(rag_service, "files_collection", files)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client, files=files, conversations=conversations)

    app.dependency_overrides.pop(get_current_user, None)


def _ids(files):
    return {d["_id"] for d in files.docs}


# --- flag OFF ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_flag_off_endpoints_unreachable(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", False)
    status_resp = await sets_env.client.get("/api/assistants/threads/sources-status")
    assert status_resp.json() == {"enabled": False}
    view = await sets_env.client.get("/api/assistants/threads/tA/sources")
    assert view.status_code == 404
    rm = await sets_env.client.post(
        "/api/assistants/threads/tA/sources/remove",
        json={"filename": "a.pdf", "confirm": True},
    )
    assert rm.status_code == 404
    # Nothing was deleted despite confirm=true.
    assert len(sets_env.files.docs) == len(_corpus())


# --- sources view ------------------------------------------------------------
@pytest.mark.asyncio
async def test_sources_view_mixed_provenance_and_states(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    resp = await sets_env.client.get("/api/assistants/threads/tA/sources")
    assert resp.status_code == 200
    rows = {s["filename"]: s for s in resp.json()["sources"]}
    assert set(rows) == {"a.pdf", "b.pdf", "c.pdf", "d.pdf"}

    a = rows["a.pdf"]
    assert a["status"] == "ready" and a["chunkCount"] == 2
    assert a["provenance"] == "mixed"
    assert a["visionChunkCount"] == 1 and a["visionPages"] == [3]
    assert a["partiallyIndexed"] is False

    b = rows["b.pdf"]
    assert b["status"] == "pending" and b["reason"] is None

    c = rows["c.pdf"]
    assert c["status"] == "failed" and c["reason"] == "Unreadable"

    d = rows["d.pdf"]
    assert d["status"] == "ready"
    assert d["provenance"] == "vision"
    assert d["partiallyIndexed"] is True
    assert d["warning"] == "2 pages could not be read"


@pytest.mark.asyncio
async def test_sources_view_excludes_other_threads_and_users(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    resp = await sets_env.client.get("/api/assistants/threads/tB/sources")
    rows = resp.json()["sources"]
    assert [s["filename"] for s in rows] == ["a.pdf"]
    assert rows[0]["chunkCount"] == 1  # tB's own copy, not tA's


# --- per-source removal ------------------------------------------------------
@pytest.mark.asyncio
async def test_dry_run_returns_counts_and_deletes_nothing(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    resp = await sets_env.client.post(
        "/api/assistants/threads/tA/sources/remove",
        json={"filename": "a.pdf"},  # confirm defaults to False
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dryRun"] is True
    assert body["chunksToDelete"] == 2
    assert body["parentDocsToDelete"] == 1
    assert body["untouched"] == {
        # bP, cP, dP, d0 -- the thread's other sources
        "otherSourcesInThread": 4,
        "otherCategoriesInThread": 0,
        # kb + uu
        "sameFilenameOtherCategories": 2,
        # xP + x0 in thread tB
        "sameFilenameOtherThreads": 2,
    }
    # Dry-run deleted nothing.
    assert _ids(sets_env.files) == {d["_id"] for d in _corpus()}


@pytest.mark.asyncio
async def test_confirmed_removal_four_way_isolation_on_survivors(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    resp = await sets_env.client.post(
        "/api/assistants/threads/tA/sources/remove",
        json={"filename": "a.pdf", "confirm": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dryRun"] is False
    assert body["deleted"] == 3  # aP + a0 + a1
    # Survivors: the thread's OTHER sources, the other thread's copy, the
    # knowledge base copy, the plain user_upload copy, and the foreign
    # user's doc carrying this very threadId.
    assert _ids(sets_env.files) == {"bP", "cP", "dP", "d0", "xP", "x0", "kb", "uu", "fA"}
    # And the fingerprint changed (Phase 2: removal is a document-set change).


@pytest.mark.asyncio
async def test_removal_of_unknown_source_404s(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    resp = await sets_env.client.post(
        "/api/assistants/threads/tA/sources/remove",
        json={"filename": "nope.pdf", "confirm": True},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_removal_from_unowned_thread_404s(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    # No conversations row for user U with this threadId -> ownership gate.
    resp = await sets_env.client.post(
        "/api/assistants/threads/tZ/sources/remove",
        json={"filename": "a.pdf", "confirm": True},
    )
    assert resp.status_code == 404
    assert _ids(sets_env.files) == {d["_id"] for d in _corpus()}


@pytest.mark.asyncio
async def test_removing_last_source_leaves_set_intact_and_empty(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    for fn in ("a.pdf", "b.pdf", "c.pdf", "d.pdf"):
        resp = await sets_env.client.post(
            "/api/assistants/threads/tA/sources/remove",
            json={"filename": fn, "confirm": True},
        )
        assert resp.status_code == 200
    view = await sets_env.client.get("/api/assistants/threads/tA/sources")
    assert view.json() == {"sources": []}
    # The conversation row (the set itself) survives.
    row = await sets_env.conversations.find_one({"userId": "U", "threadId": "tA"})
    assert row is not None and row["name"] == "Set A"


# --- Phase 2 fingerprint on add / remove -------------------------------------
@pytest.mark.asyncio
async def test_add_and_remove_change_document_fingerprint(sets_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_SETS_ENABLED", True)
    _, fp_before, _ = await rag_service.thread_document_inventory("tA", "U")

    # Adding a source is the existing upload path: it inserts a parent doc.
    await sets_env.files.insert_one({
        "_id": "nP", "userId": "U", "threadId": "tA", "category": "thread_upload",
        "filename": "new.pdf", "chunkCount": 3, "status": "processed",
    })
    _, fp_added, _ = await rag_service.thread_document_inventory("tA", "U")
    assert fp_added != fp_before

    # Removing one changes it again.
    resp = await sets_env.client.post(
        "/api/assistants/threads/tA/sources/remove",
        json={"filename": "new.pdf", "confirm": True},
    )
    assert resp.status_code == 200
    _, fp_removed, _ = await rag_service.thread_document_inventory("tA", "U")
    assert fp_removed == fp_before  # same set as before the add
    assert fp_removed != fp_added
