"""Unit tests for the History persistence layer (with an in-memory fake)."""

import copy
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.workspace import history_store

pytestmark = pytest.mark.unit


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit = None

    def sort(self, key, direction):
        self._docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, length=None):
        docs = self._docs if self._limit is None else self._docs[: self._limit]
        return [copy.deepcopy(d) for d in docs]


class FakeCollection:
    def __init__(self):
        self._docs = {}

    @staticmethod
    def _match(doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    async def insert_one(self, doc):
        _id = doc.get("_id") or ObjectId()
        stored = copy.deepcopy(doc)
        stored["_id"] = _id
        self._docs[_id] = stored
        return SimpleNamespace(inserted_id=_id)

    async def find_one(self, query, projection=None):
        for doc in self._docs.values():
            if self._match(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query, projection=None):
        return _FakeCursor([d for d in self._docs.values() if self._match(d, query)])

    async def update_one(self, query, update):
        for doc in self._docs.values():
            if self._match(doc, query):
                for k, v in update.get("$push", {}).items():
                    doc.setdefault(k, []).append(copy.deepcopy(v))
                for k, v in update.get("$set", {}).items():
                    doc[k] = copy.deepcopy(v)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    monkeypatch.setattr(history_store, "runs_collection", FakeCollection())
    monkeypatch.setattr(history_store, "threads_collection", FakeCollection())


@pytest.mark.asyncio
async def test_create_and_get_run_scoped():
    rid = await history_store.create_run(
        "alice", "cpt_interpretation", "s.CPT", {"calculator_id": "cpt_interpretation"}, {"layer_count": 3}
    )
    got = await history_store.get_run("alice", rid)
    assert got["id"] == rid
    assert got["summary"]["layer_count"] == 3
    # Wrong user -> None (isolation). Bad id -> None (clean, no crash).
    assert await history_store.get_run("bob", rid) is None
    assert await history_store.get_run("alice", "not-an-oid") is None


@pytest.mark.asyncio
async def test_list_runs_newest_first():
    r1 = await history_store.create_run("alice", "cpt_interpretation", "a.CPT", {}, {})
    r2 = await history_store.create_run("alice", "cpt_interpretation", "b.CPT", {}, {})
    ids = [r["id"] for r in await history_store.list_runs("alice")]
    assert ids == [r2, r1]
    assert await history_store.list_runs("bob") == []


@pytest.mark.asyncio
async def test_thread_append_and_get():
    tid = await history_store.create_thread("alice", "My run - 2026-07-14")
    assert await history_store.thread_exists("alice", tid) is True
    assert await history_store.append_message(
        "alice", tid, {"role": "user", "type": "text", "content": "hi"}
    ) is True

    thread = await history_store.get_thread("alice", tid)
    assert thread["title"] == "My run - 2026-07-14"
    assert thread["messages"][0]["content"] == "hi"
    assert "created_at" in thread["messages"][0]

    # Foreign user cannot append or read.
    assert await history_store.append_message("bob", tid, {"x": 1}) is False
    assert await history_store.get_thread("bob", tid) is None
