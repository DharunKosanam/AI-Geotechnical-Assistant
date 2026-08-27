"""CHAT_SHARING_ENABLED: join/leave/member routes, membership authz on
message read/write, the sidebar queries, flag-off byte-parity — and the
point of the phase: FILES ARE NEVER SHARED. Route functions called directly
with async fakes; no Mongo, no LLM.
"""

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException

from app.core import config
from app.routers import chat as chat_mod
from app.routers import threads as threads_mod
from models import User

pytestmark = pytest.mark.unit

OWNER = User(id="uA", email="a@uvic.ca", hashed_password="x",
             full_name="User A", role="user")
MEMBER = User(id="uB", email="b@uvic.ca", hashed_password="x",
              full_name="User B", role="user")
STRANGER = User(id="uC", email="c@uvic.ca", hashed_password="x",
                full_name="User C", role="user")

T0 = datetime(2026, 8, 26, 10, 0, 0)

SHARED_CONV = {"threadId": "th-shared", "userId": "uA", "name": "GRS wall",
               "isGroup": True, "members": ["uA", "uB"],
               "createdAt": T0, "updatedAt": T0}
PRIVATE_CONV = {"threadId": "th-private", "userId": "uA", "name": "private",
                "isGroup": False, "members": ["uA"],
                "createdAt": T0, "updatedAt": T0}


def _match(doc, query):
    """Minimal Mongo matcher: equality, $ne, membership-in-array."""
    for k, v in (query or {}).items():
        got = doc.get(k)
        if isinstance(v, dict):
            if "$ne" in v and got == v["$ne"]:
                return False
            if "$exists" in v and (k in doc) != bool(v["$exists"]):
                return False
        elif isinstance(got, list) and not isinstance(v, list):
            if v not in got:
                return False
        elif got != v:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, field, direction=1):
        self.docs.sort(key=lambda d: (d.get(field) is None, d.get(field)),
                       reverse=direction == -1)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield dict(d)
        return gen()


def _copy(doc):
    """Per-test isolation: list values (members!) must never be shared
    between the module-level fixtures and a test's mutations."""
    return {k: (list(v) if isinstance(v, list) else v) for k, v in doc.items()}


class FakeColl:
    """Filter-honoring fake: find/find_one apply the query; update_one
    implements $addToSet/$each/$pull/$set enough for the sharing routes."""

    def __init__(self, docs=()):
        self.docs = [_copy(d) for d in docs]
        self.queries = []
        self.updates = []

    def find(self, query=None, *a, **k):
        self.queries.append(query)
        return _Cursor([_copy(d) for d in self.docs if _match(d, query)])

    async def find_one(self, query=None, *a, **k):
        self.queries.append(query)
        for d in self.docs:
            if _match(d, query):
                return _copy(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(_copy(doc))

    async def update_one(self, filt, update, *a, **k):
        self.updates.append((filt, update))
        for d in self.docs:
            if not _match(d, filt):
                continue
            for key, val in (update.get("$set") or {}).items():
                d[key] = val
            for key, val in (update.get("$addToSet") or {}).items():
                arr = d.setdefault(key, [])
                additions = val["$each"] if isinstance(val, dict) and "$each" in val else [val]
                for item in additions:
                    if item not in arr:
                        arr.append(item)
            for key, val in (update.get("$pull") or {}).items():
                d[key] = [x for x in d.get(key, []) if x != val]
            break


@pytest.fixture()
def convs(monkeypatch):
    monkeypatch.setattr(config, "CHAT_SHARING_ENABLED", True)
    coll = FakeColl([SHARED_CONV, PRIVATE_CONV])
    monkeypatch.setattr(threads_mod, "conversations_collection", coll)
    monkeypatch.setattr(chat_mod, "conversations_collection", coll)
    return coll


@pytest.fixture()
def msgs(monkeypatch):
    # _id present on every doc: HIGHLIGHTS_ENABLED (on in the dev .env) makes
    # the history endpoint echo str(doc["_id"]) per message.
    coll = FakeColl([
        {"_id": "m1", "threadId": "th-shared", "userId": "uA", "role": "user",
         "content": "what is a GRS wall?", "createdAt": T0},
        {"_id": "m2", "threadId": "th-shared", "userId": "uA", "role": "assistant",
         "content": "a geosynthetic-reinforced soil wall", "sources": [],
         "createdAt": T0 + timedelta(seconds=1)},
        {"_id": "m3", "threadId": "th-shared", "userId": "uB", "role": "user",
         "content": "and the log spiral geometry?", "createdAt": T0 + timedelta(seconds=2)},
    ])
    monkeypatch.setattr(chat_mod, "messages_collection", coll)
    return coll


# --- join / leave / remove ----------------------------------------------------
async def test_join_unknown_thread_404(convs):
    with pytest.raises(HTTPException) as ei:
        await threads_mod.join_thread("th-nope", current_user=MEMBER)
    assert ei.value.status_code == 404


async def test_join_unshared_thread_403(convs):
    # A thread id alone grants NOTHING — the owner must have shared it.
    with pytest.raises(HTTPException) as ei:
        await threads_mod.join_thread("th-private", current_user=STRANGER)
    assert ei.value.status_code == 403
    conv = next(d for d in convs.docs if d["threadId"] == "th-private")
    assert conv["members"] == ["uA"]


async def test_join_is_idempotent_and_keeps_owner(convs):
    out = await threads_mod.join_thread("th-shared", current_user=STRANGER)
    assert out["success"] is True and out["name"] == "GRS wall"
    out2 = await threads_mod.join_thread("th-shared", current_user=STRANGER)
    assert out2["success"] is True
    conv = next(d for d in convs.docs if d["threadId"] == "th-shared")
    assert conv["members"] == ["uA", "uB", "uC"]  # no duplicate, owner intact


async def test_leave_self_only_owner_cannot_leave(convs):
    await threads_mod.leave_thread("th-shared", current_user=MEMBER)
    conv = next(d for d in convs.docs if d["threadId"] == "th-shared")
    assert conv["members"] == ["uA"]
    with pytest.raises(HTTPException) as ei:
        await threads_mod.leave_thread("th-shared", current_user=OWNER)
    assert ei.value.status_code == 403
    assert "uA" in conv["members"]


async def test_owner_removes_member_but_never_self(convs):
    with pytest.raises(HTTPException) as ei:
        await threads_mod.remove_thread_member("th-shared", "uB", current_user=MEMBER)
    assert ei.value.status_code == 403  # members cannot remove others
    out = await threads_mod.remove_thread_member("th-shared", "uB", current_user=OWNER)
    assert out["removed"] == "uB"
    conv = next(d for d in convs.docs if d["threadId"] == "th-shared")
    assert conv["members"] == ["uA"]
    with pytest.raises(HTTPException) as ei:
        await threads_mod.remove_thread_member("th-shared", "uA", current_user=OWNER)
    assert ei.value.status_code == 400  # owner ∈ members is an invariant


# --- membership authz on message read/write -----------------------------------
async def test_member_reads_full_history_ordered_by_created_at(convs, msgs):
    out = await chat_mod.get_chat_history("th-shared", current_user=MEMBER)
    assert [m["content"] for m in out["messages"]] == [
        "what is a GRS wall?",
        "a geosynthetic-reinforced soil wall",
        "and the log spiral geometry?",
    ]
    assert out["shared"] is True and out["memberCount"] == 2


async def test_non_member_read_is_403(convs, msgs):
    with pytest.raises(HTTPException) as ei:
        await chat_mod.get_chat_history("th-shared", current_user=STRANGER)
    assert ei.value.status_code == 403


async def test_non_member_post_is_403_not_500(convs, msgs):
    # The gate sits BEFORE the turn's catch-all try: the 403 must reach the
    # client as a 403 (the blanket handler flattens everything else to 500).
    from models import RAGChatRequest

    with pytest.raises(HTTPException) as ei:
        await chat_mod._run_chat_turn(
            RAGChatRequest(query="hi", threadId="th-shared"), STRANGER)
    assert ei.value.status_code == 403
    assert "not a member" in ei.value.detail


async def test_unregistered_thread_keeps_todays_behavior(convs, msgs):
    # No conversations row -> scope helper returns None -> caller-scoped read.
    out = await chat_mod.get_chat_history("th-unregistered", current_user=STRANGER)
    assert out == {"messages": [], "count": 0}


# --- sidebar queries ----------------------------------------------------------
async def test_list_threads_flag_on_marks_shared_rows(convs, monkeypatch):
    monkeypatch.setattr(threads_mod, "_thread_messages", {}, raising=False)
    out = await threads_mod.list_threads(current_user=MEMBER)
    rows = {t["threadId"]: t for t in out["threads"]}
    assert rows["th-shared"]["shared"] is True
    assert rows["th-shared"]["memberCount"] == 2
    assert "th-private" not in rows  # not owner, not member
    # Mine (owner view): own rows carry shared False.
    out_a = await threads_mod.list_threads(current_user=OWNER)
    rows_a = {t["threadId"]: t for t in out_a["threads"]}
    assert rows_a["th-shared"]["shared"] is False
    assert rows_a["th-private"]["shared"] is False


# --- flag OFF: byte-identical parity ------------------------------------------
class TestFlagOff:
    @pytest.fixture(autouse=True)
    def _off(self, convs, msgs, monkeypatch):
        monkeypatch.setattr(config, "CHAT_SHARING_ENABLED", False)
        self.convs = convs
        self.msgs = msgs

    async def test_thread_list_query_and_payload_unchanged(self):
        out = await threads_mod.list_threads(current_user=OWNER)
        # Exactly ONE query — the caller-scoped one; no members query.
        assert self.convs.queries == [{"userId": "uA"}]
        # Exactly today's keys — the stored members array NEVER leaks.
        for t in out["threads"]:
            assert sorted(t) == ["createdAt", "isGroup", "name", "threadId", "updatedAt"]

    async def test_message_read_query_and_payload_unchanged(self):
        out = await chat_mod.get_chat_history("th-shared", current_user=OWNER)
        # No conversations lookup at all (the scope helper is inert), and the
        # message query is the caller-scoped one, byte-for-byte.
        assert self.convs.queries == []
        assert self.msgs.queries == [{"threadId": "th-shared", "userId": "uA"}]
        assert sorted(out) == ["count", "messages"]

    async def test_message_post_gate_is_inert(self):
        # Flag-off the scope helper returns None WITHOUT touching the DB —
        # the write path is byte-identical to before the feature.
        result = await chat_mod._shared_thread_scope("th-shared", STRANGER)
        assert result is None
        assert self.convs.queries == []

    def test_routes_absent_flag_off(self, monkeypatch):
        import app.routers.threads as t
        from app.dependencies.auth import get_current_user  # noqa: F401

        app = FastAPI()
        app.include_router(t.router)
        if config.CHAT_SHARING_ENABLED:  # pinned False by the fixture
            app.include_router(t.sharing_router)
        paths = set(app.openapi()["paths"])
        assert not any(p.endswith("/join") or p.endswith("/leave") or "/members/" in p
                       for p in paths)


# --- THE POINT: files are never shared ----------------------------------------
A_CHUNK = {"category": "thread_upload", "threadId": "th-shared", "userId": "uA",
           "chunkIndex": 0, "filename": "zhang2019.pdf",
           "text": "log spiral geometry formulation"}


async def test_member_cannot_retrieve_owners_upload_through_shared_thread(convs, monkeypatch):
    """User B is a MEMBER of user A's shared thread. A uploaded a personal
    document into it. B's retrieval — driven through the REAL
    sample_thread_documents over a filter-honoring store — must return
    nothing: joining shares MESSAGES only (FIPPA boundary)."""
    from app.services import rag_service

    store = FakeColl([A_CHUNK])
    monkeypatch.setattr(rag_service, "files_collection", store)

    got_b = await rag_service.sample_thread_documents("th-shared", user_id="uB")
    assert got_b == []            # B retrieves NOTHING of A's
    got_a = await rag_service.sample_thread_documents("th-shared", user_id="uA")
    assert len(got_a) == 1        # the isolation is per-user, not a dead store
    assert got_a[0]["filename"] == "zhang2019.pdf"


def test_thread_scope_filter_is_sharing_blind(monkeypatch):
    """The retrieval filter must be identical whether sharing is on or off —
    membership can never widen it."""
    from app.services.rag_service import _thread_scope_filter

    monkeypatch.setattr(config, "CHAT_SHARING_ENABLED", True)
    on = _thread_scope_filter("uB", "th-shared")
    monkeypatch.setattr(config, "CHAT_SHARING_ENABLED", False)
    off = _thread_scope_filter("uB", "th-shared")
    assert on == off
    assert on["userId"] == "uB"   # userId is IN the query — the boundary
    assert not _match(A_CHUNK, on)  # A's chunk does not match B's filter


# --- flag ON: the two-user scenario -------------------------------------------
async def test_share_join_post_both_see_full_history(convs, msgs):
    # A shared th-shared (isGroup True in the fixture); C joins…
    await threads_mod.join_thread("th-shared", current_user=STRANGER)
    # …both "post" (the persistence the chat turn performs, gate-checked):
    conv = await chat_mod._shared_thread_scope("th-shared", STRANGER)
    assert conv is not None
    await msgs.insert_one({"_id": "m4", "threadId": "th-shared", "userId": "uC",
                           "role": "user", "content": "can I book the interrogator?",
                           "createdAt": T0 + timedelta(seconds=3)})
    await msgs.insert_one({"_id": "m5", "threadId": "th-shared", "userId": "uA",
                           "role": "user", "content": "yes after Friday",
                           "createdAt": T0 + timedelta(seconds=4)})
    # …and BOTH read the same, complete, createdAt-ordered history.
    for caller in (OWNER, STRANGER):
        out = await chat_mod.get_chat_history("th-shared", current_user=caller)
        assert [m["content"] for m in out["messages"]] == [
            "what is a GRS wall?",
            "a geosynthetic-reinforced soil wall",
            "and the log spiral geometry?",
            "can I book the interrogator?",
            "yes after Friday",
        ]
    assert out["memberCount"] == 3
