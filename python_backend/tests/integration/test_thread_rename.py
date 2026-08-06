"""Part 1 (unflagged) rename-hardening tests for PUT /api/assistants/threads/history.

The live path previously persisted empty names, reported success for
nonexistent threads, and flattened its own 400 into a 500. These tests pin
the hardened contract, and prove a rename is invisible to everything
derived: threadId, the Phase 2 document fingerprint, retrieval scoping, and
the Phase 4 scope note inputs.
"""

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from app.dependencies.auth import get_current_user
from app.main import app
from app.services import rag_service
from models import User
import app.routers.chat as chat_mod
import app.routers.threads as threads_mod

pytestmark = pytest.mark.integration


def _get_path(doc, dotted):
    val = doc
    for part in dotted.split("."):
        val = val.get(part) if isinstance(val, dict) else None
    return val


def _has_path(doc, dotted):
    val = doc
    for part in dotted.split("."):
        if not isinstance(val, dict) or part not in val:
            return False
        val = val[part]
    return True


def _matches(doc, filt):
    for key, cond in filt.items():
        if isinstance(cond, dict) and ("$exists" in cond or "$ne" in cond):
            if "$exists" in cond and _has_path(doc, key) != cond["$exists"]:
                return False
            if "$ne" in cond and _get_path(doc, key) == cond["$ne"]:
                return False
        elif _get_path(doc, key) != cond:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs
        self._i = 0

    def sort(self, *a, **k):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeColl:
    def __init__(self, docs=()):
        self.docs = [dict(d) for d in docs]
        self.update_calls = []
        self.write_count = 0

    def find(self, filt, projection=None):
        return _Cursor([d for d in self.docs if _matches(d, filt)])

    async def find_one(self, filt, projection=None):
        for d in self.docs:
            if _matches(d, filt):
                return d
        return None

    async def count_documents(self, filt):
        return sum(1 for d in self.docs if _matches(d, filt))

    async def update_one(self, filt, update):
        self.update_calls.append((filt, update))
        self.write_count += 1
        for d in self.docs:
            if _matches(d, filt):
                d.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc):
        self.write_count += 1
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="id")

    async def delete_one(self, filt):
        self.write_count += 1
        for i, d in enumerate(self.docs):
            if _matches(d, filt):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, filt):
        self.write_count += 1
        keep = [d for d in self.docs if not _matches(d, filt)]
        n = len(self.docs) - len(keep)
        self.docs = keep
        return SimpleNamespace(deleted_count=n)


_PARENT_DOC = {
    "userId": "U", "threadId": "tA", "category": "thread_upload",
    "filename": "a.pdf", "chunkCount": 2, "status": "processed",
}


@pytest_asyncio.fixture
async def rename_env(monkeypatch):
    fake_user = User(id="U", email="u@example.com", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: fake_user

    conversations = _FakeColl([
        {"userId": "U", "threadId": "tA", "name": "Old name",
         "isGroup": False, "createdAt": datetime(2026, 1, 1)},
        {"userId": "V", "threadId": "tV", "name": "Foreign", "isGroup": False},
    ])
    files = _FakeColl([dict(_PARENT_DOC)])
    messages = _FakeColl()
    monkeypatch.setattr(threads_mod, "conversations_collection", conversations)
    monkeypatch.setattr(threads_mod, "files_collection", files)
    monkeypatch.setattr(threads_mod, "messages_collection", messages)
    # The fingerprint reads rag_service's module-global collection.
    monkeypatch.setattr(rag_service, "files_collection", files)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(
            client=client, conversations=conversations,
            files=files, messages=messages,
        )

    app.dependency_overrides.pop(get_current_user, None)


async def _rename(client, name, thread="tA"):
    return await client.put(
        "/api/assistants/threads/history",
        json={"threadId": thread, "newName": name},
    )


@pytest.mark.asyncio
async def test_rename_persists_trimmed_name(rename_env):
    resp = await _rename(rename_env.client, "  Quarry Source Set  ")
    assert resp.status_code == 200
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    assert row["name"] == "Quarry Source Set"
    # threadId never rewritten: the $set carries only name and updatedAt.
    filt, update = rename_env.conversations.update_calls[-1]
    assert filt == {"userId": "U", "threadId": "tA"}
    assert set(update["$set"]) == {"name", "updatedAt"}
    assert row["threadId"] == "tA"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
async def test_empty_or_whitespace_name_rejected(rename_env, bad):
    resp = await _rename(rename_env.client, bad)
    assert resp.status_code == 400
    # Nothing was persisted -- no update call reached the collection.
    assert rename_env.conversations.update_calls == []
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    assert row["name"] == "Old name"


@pytest.mark.asyncio
async def test_overlong_name_rejected_and_cap_boundary_allowed(rename_env):
    too_long = "x" * (threads_mod.THREAD_NAME_MAX_CHARS + 1)
    resp = await _rename(rename_env.client, too_long)
    assert resp.status_code == 400
    at_cap = "y" * threads_mod.THREAD_NAME_MAX_CHARS
    resp2 = await _rename(rename_env.client, at_cap)
    assert resp2.status_code == 200
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    assert row["name"] == at_cap


# --- Conditional rename (first-action auto-title) -------------------------
# The format-click title passes expectedCurrentName so it can only ever
# replace the auto-timestamp placeholder ensureThread registered. These pin
# the four first-action scenarios at the API layer, where the guarantee is
# atomic (name is part of the update filter -- no read-then-write window).

_PLACEHOLDER = "8/5/2026, 4:42:07 PM"


async def _conditional_rename(client, new_name, expected, thread="tA"):
    return await client.put(
        "/api/assistants/threads/history",
        json={
            "threadId": thread,
            "newName": new_name,
            "expectedCurrentName": expected,
        },
    )


@pytest.mark.asyncio
async def test_first_format_action_titles_placeholder_thread(rename_env):
    """Format click as first action: the placeholder is replaced."""
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    row["name"] = _PLACEHOLDER
    resp = await _conditional_rename(
        rename_env.client, "Briefing doc - a", _PLACEHOLDER
    )
    assert resp.status_code == 200
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    assert row["name"] == "Briefing doc - a"
    # The guard is in the filter itself, not a separate read.
    filt, _ = rename_env.conversations.update_calls[-1]
    assert filt == {"userId": "U", "threadId": "tA", "name": _PLACEHOLDER}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_name",
    [
        "Briefing doc - a",       # second format click: first title stands
        "Slope Stability Query",  # thread already titled by a chat message
        "My project notes",       # user-renamed thread
    ],
)
async def test_conditional_rename_never_overwrites_existing_title(
    rename_env, current_name
):
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    row["name"] = current_name
    resp = await _conditional_rename(
        rename_env.client, "FAQ - a", _PLACEHOLDER
    )
    assert resp.status_code == 409
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    assert row["name"] == current_name


@pytest.mark.asyncio
async def test_conditional_rename_keeps_404_contract(rename_env):
    """A nonexistent (or foreign) thread is still 404, not 409."""
    resp = await _conditional_rename(
        rename_env.client, "FAQ - a", _PLACEHOLDER, thread="t-missing"
    )
    assert resp.status_code == 404
    resp2 = await _conditional_rename(
        rename_env.client, "FAQ - a", _PLACEHOLDER, thread="tV"
    )
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_unconditional_rename_semantics_unchanged(rename_env):
    """Requests without expectedCurrentName keep the pre-existing contract:
    unconditional update, filter carries only userId + threadId."""
    resp = await _rename(rename_env.client, "Renamed unconditionally")
    assert resp.status_code == 200
    filt, _ = rename_env.conversations.update_calls[-1]
    assert filt == {"userId": "U", "threadId": "tA"}


@pytest.mark.asyncio
async def test_chat_title_replaces_placeholder(rename_env):
    """Chat path parity: the LLM-generated first-message title lands on a
    thread still carrying the placeholder, via the same conditional PUT."""
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    row["name"] = _PLACEHOLDER
    resp = await _conditional_rename(
        rename_env.client, "Slope Stability Query", _PLACEHOLDER
    )
    assert resp.status_code == 200
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    assert row["name"] == "Slope Stability Query"


@pytest.mark.asyncio
async def test_chat_title_loses_to_user_rename_race(rename_env):
    """The chat-path race the guard exists for: the user renames (real
    unconditional sidebar PUT) while the first answer streams; the title's
    conditional PUT then arrives and must lose."""
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    row["name"] = _PLACEHOLDER
    resp = await _rename(rename_env.client, "My project notes")
    assert resp.status_code == 200
    resp2 = await _conditional_rename(
        rename_env.client, "Slope Stability Query", _PLACEHOLDER
    )
    assert resp2.status_code == 409
    row = await rename_env.conversations.find_one({"threadId": "tA"})
    assert row["name"] == "My project notes"


@pytest.mark.asyncio
async def test_nonexistent_thread_returns_404(rename_env):
    resp = await _rename(rename_env.client, "New name", thread="missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_other_users_thread_returns_404(rename_env):
    # The filter carries userId, so another user's thread matches nothing.
    resp = await _rename(rename_env.client, "Hijack", thread="tV")
    assert resp.status_code == 404
    row = await rename_env.conversations.find_one({"threadId": "tV"})
    assert row["name"] == "Foreign"


@pytest.mark.asyncio
async def test_invalid_threadid_is_400_not_500(rename_env):
    resp = await _rename(rename_env.client, "Name", thread="null")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rename_does_not_touch_fingerprint_files_or_retrieval(rename_env):
    has1, fp1, states1 = await rag_service.thread_document_inventory("tA", "U")
    scope_before = rag_service._thread_scope_filter("U", "tA")

    resp = await _rename(rename_env.client, "Renamed set")
    assert resp.status_code == 200

    has2, fp2, states2 = await rag_service.thread_document_inventory("tA", "U")
    assert (has1, fp1, states1) == (has2, fp2, states2)  # Phase 2 cache key intact
    assert rag_service._thread_scope_filter("U", "tA") == scope_before
    # The rename wrote to conversations only -- files and messages saw
    # zero writes, so retrieval and the Phase 4 scope note inputs (which
    # read files) cannot have been disturbed.
    assert rename_env.files.write_count == 0
    assert rename_env.messages.write_count == 0
