"""Message highlights + notes (HIGHLIGHTS_ENABLED) -- Phase 1 backend.

Deterministic, no DB/network: the three collections the router touches are
swapped for tiny in-memory fakes and the route coroutines are called directly
with a fake user. Covers:
  - anchor validation (bounds, blank/oversized text, UTF-16 offsets,
    markdown-tolerant match, mismatch detection)
  - ownership: thread not owned, message from another thread/user, non-
    assistant message, highlight of another user (update + delete)
  - flag-off: routes ABSENT from a fresh app; history payload carries no
    "id"; /api/upload/config carries no "highlights" field
"""
import copy
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import config
from app.routers import chat as chat_router
from app.routers import files as files_router
from app.routers import highlights as hl

pytestmark = pytest.mark.unit


# --- in-memory motor stand-in ------------------------------------------------

class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def __aiter__(self):
        self._it = iter([copy.deepcopy(d) for d in self._docs])
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs=()):
        self.docs = {}
        for d in docs:
            self.docs[d["_id"]] = copy.deepcopy(d)
        self.deleted = []

    @staticmethod
    def _match(doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    async def find_one(self, query, projection=None):
        for d in self.docs.values():
            if self._match(d, query):
                return copy.deepcopy(d)
        return None

    def find(self, query, projection=None):
        return _Cursor(d for d in self.docs.values() if self._match(d, query))

    async def insert_one(self, doc):
        _id = doc.get("_id") or ObjectId()
        stored = copy.deepcopy(doc)
        stored["_id"] = _id
        self.docs[_id] = stored
        return SimpleNamespace(inserted_id=_id)

    async def update_one(self, query, update):
        for d in self.docs.values():
            if self._match(d, query):
                d.update(copy.deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_one(self, query):
        for k, d in list(self.docs.items()):
            if self._match(d, query):
                del self.docs[k]
                self.deleted.append(query)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


OWNER = SimpleNamespace(id="user-owner")
OTHER = SimpleNamespace(id="user-other")
THREAD = "thread_abc"
CONTENT = "The **bearing capacity** of soil\n  continues here \\* with $\\phi = 30$ deg &amp; more."
MSG_ID = ObjectId()
USER_MSG_ID = ObjectId()
OTHER_MSG_ID = ObjectId()


@pytest.fixture
def db(monkeypatch):
    conversations = FakeCollection([
        {"_id": ObjectId(), "userId": OWNER.id, "threadId": THREAD, "name": "t"},
        {"_id": ObjectId(), "userId": OTHER.id, "threadId": "thread_other", "name": "o"},
    ])
    messages = FakeCollection([
        {"_id": MSG_ID, "threadId": THREAD, "userId": OWNER.id, "role": "assistant", "content": CONTENT},
        {"_id": USER_MSG_ID, "threadId": THREAD, "userId": OWNER.id, "role": "user", "content": "q?"},
        {"_id": OTHER_MSG_ID, "threadId": "thread_other", "userId": OTHER.id, "role": "assistant", "content": CONTENT},
    ])
    highlights = FakeCollection()
    monkeypatch.setattr(hl, "conversations_collection", conversations)
    monkeypatch.setattr(hl, "messages_collection", messages)
    monkeypatch.setattr(hl, "highlights_collection", highlights)
    monkeypatch.setattr(config, "HIGHLIGHTS_ENABLED", True)
    return SimpleNamespace(conversations=conversations, messages=messages, highlights=highlights)


def _req(**kw):
    base = dict(messageId=str(MSG_ID), startOffset=6, endOffset=22,
                selectedText="bearing capacity", colour="yellow", note="")
    base.update(kw)
    return hl.HighlightCreateRequest(**base)


# --- validate_anchor ---------------------------------------------------------

def test_anchor_accepts_span_across_markdown_syntax():
    # source [4,22) = "**bearing capacity" ; rendered selection "bearing capacity"
    assert hl.validate_anchor(CONTENT, 4, 22, "bearing capacity") is None
    # spanning bold end + escape + entity: rendered "capacity of soil continues here * with"
    end = CONTENT.index("$")
    assert hl.validate_anchor(CONTENT, 14, end, "capacity of soil\ncontinues here * with") is None
    # entity: rendered "&" vs source "&amp;" -- the extra "amp" word is tolerated
    s = CONTENT.index("deg")
    assert hl.validate_anchor(CONTENT, s, len(CONTENT), "deg & more.") is None
    # link target and TeX are extra source words that render to nothing/glyphs
    src = "See [Terzaghi](http://x.org/t) for $\\phi$ angle"
    assert hl.validate_anchor(src, 4, len(src), "Terzaghi for angle") is None
    # escaped underscore renders unescaped; words agree on both sides
    src = "use snake\\_case here"
    assert hl.validate_anchor(src, 4, len(src), "snake_case here") is None
    # a selection that starts mid-word is cut at the same place on both sides
    assert hl.validate_anchor(CONTENT, 7, 22, "earing capacity") is None
    # entity that decodes to a LETTER: prose renders "&phi;" as φ ...
    src = "angle &phi; of 30"
    assert hl.validate_anchor(src, 0, len(src), "angle φ of 30") is None
    # ... while inside a code span the same bytes render literally
    src = "use `&amp;` here"
    assert hl.validate_anchor(src, 5, len(src), "&amp; here") is None


@pytest.mark.parametrize("start,end,msg", [
    (-1, 5, "startOffset must be >= 0"),
    (5, 5, "startOffset must be >= 0 and < endOffset"),
    (9, 5, "startOffset must be >= 0 and < endOffset"),
    (0, len(CONTENT) + 1, "beyond the end"),
])
def test_anchor_rejects_bad_bounds(start, end, msg):
    err = hl.validate_anchor(CONTENT, start, end, "x")
    assert err and msg in err


def test_anchor_rejects_blank_or_oversized_selection():
    assert "blank" in hl.validate_anchor(CONTENT, 0, 5, "   ")
    assert "blank" in hl.validate_anchor(CONTENT, 0, 5, "")
    assert "at most" in hl.validate_anchor("a" * 10000, 0, 9000, "a" * 5001)


def test_anchor_rejects_selection_longer_than_range():
    # rendering only removes characters, so rendered text can never exceed the slice
    assert "longer than the offset range" in hl.validate_anchor(CONTENT, 6, 10, "bearing capacity")


def test_anchor_detects_selected_text_mismatch():
    # right length, wrong place: the slice's words are cut differently
    err = hl.validate_anchor(CONTENT, 0, 16, "bearing capacity")
    assert err and "does not match" in err
    # shifted by a few characters: last word is truncated in the slice
    err = hl.validate_anchor(CONTENT, 3, 19, "bearing capacity")
    assert err and "does not match" in err
    # garbage from another message
    err = hl.validate_anchor(CONTENT, 6, 22, "liquefaction")
    assert err and "does not match" in err
    # right words, wrong order
    err = hl.validate_anchor(CONTENT, 6, 22, "capacity bearing")
    assert err and "does not match" in err


def test_anchor_rejects_non_string_content():
    legacy = [{"type": "text", "text": {"value": "x"}}]
    assert "not highlightable" in hl.validate_anchor(legacy, 0, 1, "x")


def test_anchor_offsets_are_utf16_units():
    # U+1F600 is 2 UTF-16 units but 1 Python code point; the offsets a JS client
    # sends for the text after it must resolve to the same characters.
    content = "\U0001F600 friction angle"
    assert hl.utf16_len(content) == len(content) + 1
    start = content.index("friction") + 1  # JS index of "f"
    assert hl.utf16_slice(content, start, start + 8) == "friction"
    assert hl.validate_anchor(content, start, start + 8, "friction") is None
    # a Python-code-point offset (off by one) fails the match
    assert hl.validate_anchor(content, start - 1, start + 7, "friction") is not None


# --- routes: create / list / update / delete ---------------------------------

async def test_create_and_list_roundtrip(db):
    out = await hl.create_highlight(THREAD, _req(note="check units"), current_user=OWNER)
    h = out["highlight"]
    assert h["threadId"] == THREAD and h["messageId"] == str(MSG_ID) and h["userId"] == OWNER.id
    assert (h["startOffset"], h["endOffset"], h["selectedText"]) == (6, 22, "bearing capacity")
    assert h["colour"] == "yellow" and h["note"] == "check units"
    assert h["createdAt"] and h["updatedAt"]
    stored = db.highlights.docs[ObjectId(h["id"])]
    assert stored["userId"] == OWNER.id and stored["threadId"] == THREAD

    listed = await hl.list_highlights(THREAD, current_user=OWNER)
    assert [x["id"] for x in listed["highlights"]] == [h["id"]]


async def test_create_rejects_bad_anchor_colour_note(db):
    with pytest.raises(HTTPException) as e:
        await hl.create_highlight(THREAD, _req(selectedText="liquefaction"), current_user=OWNER)
    assert e.value.status_code == 422 and "does not match" in e.value.detail
    with pytest.raises(HTTPException) as e:
        await hl.create_highlight(THREAD, _req(colour="mauve"), current_user=OWNER)
    assert e.value.status_code == 422 and "colour" in e.value.detail
    with pytest.raises(HTTPException) as e:
        await hl.create_highlight(THREAD, _req(note="n" * 2001), current_user=OWNER)
    assert e.value.status_code == 422 and "note" in e.value.detail
    assert db.highlights.docs == {}


async def test_thread_ownership_enforced_on_every_route(db):
    # OTHER does not own THREAD -> 404 everywhere, nothing written
    for call in (
        lambda: hl.create_highlight(THREAD, _req(), current_user=OTHER),
        lambda: hl.list_highlights(THREAD, current_user=OTHER),
        lambda: hl.update_highlight(THREAD, str(ObjectId()), hl.HighlightUpdateRequest(note="x"), current_user=OTHER),
        lambda: hl.delete_highlight(THREAD, str(ObjectId()), current_user=OTHER),
    ):
        with pytest.raises(HTTPException) as e:
            await call()
        assert e.value.status_code == 404 and e.value.detail == "Thread not found"
    assert db.highlights.docs == {}


async def test_message_must_belong_to_thread_and_be_assistant(db):
    # another user's message id, even though OWNER owns THREAD -> 404
    with pytest.raises(HTTPException) as e:
        await hl.create_highlight(THREAD, _req(messageId=str(OTHER_MSG_ID)), current_user=OWNER)
    assert e.value.status_code == 404 and e.value.detail == "Message not found"
    # malformed id -> 404, not 500
    with pytest.raises(HTTPException) as e:
        await hl.create_highlight(THREAD, _req(messageId="not-an-oid"), current_user=OWNER)
    assert e.value.status_code == 404
    # a user-role message cannot carry highlights
    with pytest.raises(HTTPException) as e:
        await hl.create_highlight(THREAD, _req(messageId=str(USER_MSG_ID), startOffset=0, endOffset=2, selectedText="q?"), current_user=OWNER)
    assert e.value.status_code == 422
    assert db.highlights.docs == {}


async def test_update_and_delete_only_by_owner(db):
    created = (await hl.create_highlight(THREAD, _req(), current_user=OWNER))["highlight"]
    hid = created["id"]

    # OTHER cannot reach it even via their own thread id (highlight is scoped to THREAD+OWNER)
    with pytest.raises(HTTPException) as e:
        await hl.update_highlight("thread_other", hid, hl.HighlightUpdateRequest(note="hi"), current_user=OTHER)
    assert e.value.status_code == 404 and e.value.detail == "Highlight not found"
    with pytest.raises(HTTPException) as e:
        await hl.delete_highlight("thread_other", hid, current_user=OTHER)
    assert e.value.status_code == 404
    assert ObjectId(hid) in db.highlights.docs

    # owner updates note + colour; unknown colour rejected; empty patch rejected
    out = await hl.update_highlight(THREAD, hid, hl.HighlightUpdateRequest(note="revisit", colour="green"), current_user=OWNER)
    assert out["highlight"]["note"] == "revisit" and out["highlight"]["colour"] == "green"
    assert db.highlights.docs[ObjectId(hid)]["colour"] == "green"
    with pytest.raises(HTTPException) as e:
        await hl.update_highlight(THREAD, hid, hl.HighlightUpdateRequest(colour="mauve"), current_user=OWNER)
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await hl.update_highlight(THREAD, hid, hl.HighlightUpdateRequest(), current_user=OWNER)
    assert e.value.status_code == 422

    # owner deletes; second delete is 404
    assert (await hl.delete_highlight(THREAD, hid, current_user=OWNER)) == {"success": True}
    assert ObjectId(hid) not in db.highlights.docs
    with pytest.raises(HTTPException) as e:
        await hl.delete_highlight(THREAD, hid, current_user=OWNER)
    assert e.value.status_code == 404


# --- flag off: routes absent, payloads byte-identical -----------------------

def _highlight_paths(app):
    """Registered highlight routes as {path: [METHODS]} from the app's own
    OpenAPI route table (FastAPI >= 0.13x wraps included routers, so app.routes
    can no longer be walked for paths directly)."""
    return {
        path: sorted(m.upper() for m in ops)
        for path, ops in app.openapi()["paths"].items()
        if "highlights" in path
    }


_PROBES = (
    ("GET", "/api/assistants/threads/t1/highlights"),
    ("GET", "/api/assistants/threads/t1/highlights/export?format=md"),
    ("POST", "/api/assistants/threads/t1/highlights"),
    ("PATCH", "/api/assistants/threads/t1/highlights/h1"),
    ("DELETE", "/api/assistants/threads/t1/highlights/h1"),
)


def test_flag_off_registers_no_routes(monkeypatch):
    monkeypatch.setattr(config, "HIGHLIGHTS_ENABLED", False)
    app = FastAPI()
    before = app.openapi()["paths"].copy()
    hl.register(app)
    app.openapi_schema = None  # force regeneration
    assert app.openapi()["paths"] == before
    assert _highlight_paths(app) == {}
    # Behavioural proof of ABSENCE: unauthenticated probes hit no route at all
    # (404), rather than an existing route's auth gate (401).
    client = TestClient(app)
    for method, path in _PROBES:
        assert client.request(method, path).status_code == 404, (method, path)


def test_flag_on_registers_the_four_routes(monkeypatch):
    monkeypatch.setattr(config, "HIGHLIGHTS_ENABLED", True)
    app = FastAPI()
    hl.register(app)
    assert _highlight_paths(app) == {
        "/api/assistants/threads/{thread_id}/highlights": ["GET", "POST"],
        "/api/assistants/threads/{thread_id}/highlights/export": ["GET"],
        "/api/assistants/threads/{thread_id}/highlights/{highlight_id}": ["DELETE", "PATCH"],
    }
    # Same probes now reach the routes and stop at get_current_user (401).
    client = TestClient(app)
    for method, path in _PROBES:
        assert client.request(method, path).status_code == 401, (method, path)


async def test_history_payload_carries_id_only_when_flag_on(monkeypatch):
    from datetime import datetime
    ts = datetime(2026, 8, 17, 12, 0, 0)
    messages = FakeCollection([
        {"_id": MSG_ID, "threadId": THREAD, "userId": OWNER.id, "role": "assistant",
         "content": "answer", "sources": [], "createdAt": ts},
    ])
    monkeypatch.setattr(chat_router, "messages_collection", messages)

    monkeypatch.setattr(config, "HIGHLIGHTS_ENABLED", False)
    off = await chat_router.get_chat_history(THREAD, current_user=OWNER)
    # exact equality: no new key leaks out flag-off
    assert off == {"messages": [{"role": "assistant", "content": "answer", "sources": [],
                                 "createdAt": ts.isoformat()}], "count": 1}

    monkeypatch.setattr(config, "HIGHLIGHTS_ENABLED", True)
    on = await chat_router.get_chat_history(THREAD, current_user=OWNER)
    assert on["messages"][0]["id"] == str(MSG_ID)
    assert {k: v for k, v in on["messages"][0].items() if k != "id"} == off["messages"][0]


async def test_upload_config_capability_only_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(config, "DIAGRAM_EDITOR_ENABLED", False)
    monkeypatch.setattr(config, "HIGHLIGHTS_ENABLED", False)
    off = await files_router.upload_config(current_user=object())
    assert "highlights" not in off
    assert off == {"extensions": [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".pptx"],
                   "label": "PDF, DOCX, XLSX, XLS, CSV, PPTX"}
    monkeypatch.setattr(config, "HIGHLIGHTS_ENABLED", True)
    on = await files_router.upload_config(current_user=object())
    assert on["highlights"] is True
    assert {k: v for k, v in on.items() if k != "highlights"} == off
