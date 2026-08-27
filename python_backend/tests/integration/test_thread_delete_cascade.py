"""Phase 7/8 Part B: thread-deletion cascade for uploaded thread documents.

Drives DELETE /api/assistants/threads/history with auth overridden and fake
collections, asserting on WHAT SURVIVES deletion across the same four-way corpus
as the isolation test (thread A, thread B, knowledge_base, user_upload) plus a
foreign-user doc sharing thread A's id (to prove the userId scope).
"""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from app.dependencies.auth import get_current_user
from app.main import app
from models import User
import app.routers.threads as threads_mod

pytestmark = pytest.mark.integration


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _match(self, d, flt):
        for k, v in flt.items():
            if isinstance(v, dict):
                if "$ne" in v:
                    if d.get(k) == v["$ne"]:
                        return False
                elif "$exists" in v:
                    if (k in d) != v["$exists"]:
                        return False
                else:
                    return False  # unsupported operator -> no match
            elif d.get(k) != v:
                return False
        return True

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if self._match(d, flt):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, flt):
        keep = [d for d in self.docs if not self._match(d, flt)]
        n = len(self.docs) - len(keep)
        self.docs = keep
        return SimpleNamespace(deleted_count=n)

    async def count_documents(self, flt):
        return sum(1 for d in self.docs if self._match(d, flt))


def _corpus():
    # ids: a* = thread A, b* = thread B, kb = shared index, uu = plain upload,
    # xA = a DIFFERENT user's doc that happens to carry thread A's id.
    return [
        {"_id": "aP", "category": "thread_upload", "threadId": "thread-A", "userId": "U", "docType": "file"},
        {"_id": "aC", "category": "thread_upload", "threadId": "thread-A", "userId": "U", "chunkIndex": 0},
        {"_id": "bP", "category": "thread_upload", "threadId": "thread-B", "userId": "U", "docType": "file"},
        {"_id": "bC", "category": "thread_upload", "threadId": "thread-B", "userId": "U", "chunkIndex": 0},
        {"_id": "kb", "category": "knowledge_base"},
        {"_id": "uu", "category": "user_upload", "userId": "U"},
        {"_id": "xA", "category": "thread_upload", "threadId": "thread-A", "userId": "OTHER"},
    ]


_ALL_IDS = {"aP", "aC", "bP", "bC", "kb", "uu", "xA"}


@pytest_asyncio.fixture
async def delete_env(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: User(
        id="U", email="u@example.com", hashed_password="x"
    )
    conv = _FakeColl([
        {"_id": "cA", "userId": "U", "threadId": "thread-A"},
        {"_id": "cB", "userId": "U", "threadId": "thread-B"},
        {"_id": "cE", "userId": "U", "threadId": "thread-EMPTY"},
    ])
    msgs = _FakeColl([{"_id": "m1", "userId": "U", "threadId": "thread-A"}])
    files = _FakeColl(_corpus())
    monkeypatch.setattr(threads_mod, "conversations_collection", conv)
    monkeypatch.setattr(threads_mod, "messages_collection", msgs)
    monkeypatch.setattr(threads_mod, "files_collection", files)
    # HIGHLIGHTS cascade (threads.py delete_thread) runs unconditionally; the
    # fixture predates it and left the REAL highlights_collection in place, so
    # every delete test issued a delete_many against live Atlas (audit fix
    # 2026-08-26). Empty fake: the cascade is a no-op, as asserted elsewhere.
    monkeypatch.setattr(threads_mod, "highlights_collection", _FakeColl([]))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client, files=files, conv=conv, msgs=msgs)

    app.dependency_overrides.pop(get_current_user, None)


async def _delete(client, thread_id):
    return await client.request(
        "DELETE", "/api/assistants/threads/history", json={"threadId": thread_id}
    )


@pytest.mark.asyncio
async def test_delete_thread_a_removes_only_thread_a_owned_docs(delete_env):
    resp = await _delete(delete_env.client, "thread-A")
    assert resp.status_code == 200
    assert resp.json()["deleted_thread_documents"] == 2  # aP + aC only

    survivors = {d["_id"] for d in delete_env.files.docs}
    # thread A's own parent + chunk are gone ...
    assert "aP" not in survivors and "aC" not in survivors
    # ... but NOTHING else is: thread B, knowledge_base, plain user_upload, and
    # the foreign user's same-threadId doc all survive.
    assert survivors == {"bP", "bC", "kb", "uu", "xA"}


@pytest.mark.asyncio
async def test_delete_empty_thread_removes_zero_docs(delete_env):
    # A thread with no uploaded documents must delete zero file docs -- no
    # accidental broad match on an empty/absent threadId.
    resp = await _delete(delete_env.client, "thread-EMPTY")
    assert resp.status_code == 200
    assert resp.json()["deleted_thread_documents"] == 0
    assert {d["_id"] for d in delete_env.files.docs} == _ALL_IDS  # nothing removed


@pytest.mark.asyncio
async def test_delete_thread_b_leaves_thread_a(delete_env):
    # Symmetry: deleting thread B never touches thread A's docs.
    resp = await _delete(delete_env.client, "thread-B")
    assert resp.status_code == 200
    assert resp.json()["deleted_thread_documents"] == 2  # bP + bC
    survivors = {d["_id"] for d in delete_env.files.docs}
    assert "bP" not in survivors and "bC" not in survivors
    assert {"aP", "aC", "xA", "kb", "uu"} <= survivors
