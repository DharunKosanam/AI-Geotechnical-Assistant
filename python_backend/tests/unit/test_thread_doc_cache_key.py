"""Phase 2 unit tests: THREAD_DOC cache invalidation on document-set change,
plus the two items carried from Phase 4 (scope-note flag matrix across
transports; excluded-document rendering).

The fingerprint is sha256 over sorted (filename, chunkCount) parent-doc pairs:
a bare count would miss an add-plus-delete, a bare timestamp would miss a
re-ingest. Only the THREAD_DOC key changes; the other three modes' keys are
asserted byte-exact.
"""

import pytest

from app.core import config
from app.routers import chat as chat_mod
from app.routers.chat import _mode_cache_key, _thread_scope_note
from app.services import rag_service
from models import RAGChatRequest, RAGChatResponse, User

pytestmark = pytest.mark.unit

TID = "thread-C"
UID = "U"


# --- fakes ------------------------------------------------------------------
def _matches(doc, flt):
    for k, v in flt.items():
        if isinstance(v, dict) and "$exists" in v:
            if (k in doc) != v["$exists"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _AsyncIter:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeFiles:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, flt, projection=None):
        return _AsyncIter([d for d in self.docs if _matches(d, flt)])


def _parent(filename, chunk_count):
    """A thread-upload PARENT doc (no chunkIndex)."""
    return {
        "_id": f"p-{filename}",
        "category": "thread_upload",
        "threadId": TID,
        "userId": UID,
        "filename": filename,
        "chunkCount": chunk_count,
        "docType": "file",
    }


def _chunk(filename, i):
    """A chunk doc -- must NOT participate in the fingerprint."""
    return {
        "_id": f"c-{filename}-{i}",
        "category": "thread_upload",
        "threadId": TID,
        "userId": UID,
        "filename": filename,
        "chunkIndex": i,
    }


async def _fp(monkeypatch, parents, extra=()):
    docs = list(parents) + list(extra)
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    has, fp = await rag_service.thread_document_inventory(TID, UID)
    return has, fp


# --- fingerprint: changes exactly when the document set changes --------------
async def test_upload_changes_fingerprint(monkeypatch):
    base = [_parent("a.pdf", 40), _parent("s.pdf", 56)]
    _, fp1 = await _fp(monkeypatch, base)
    _, fp2 = await _fp(monkeypatch, base + [_parent("new.pdf", 12)])
    assert fp1 and fp2 and fp1 != fp2


async def test_delete_changes_fingerprint(monkeypatch):
    _, fp1 = await _fp(monkeypatch, [_parent("a.pdf", 40), _parent("s.pdf", 56)])
    _, fp2 = await _fp(monkeypatch, [_parent("a.pdf", 40)])
    assert fp1 != fp2


async def test_reingest_same_filename_different_chunkcount_changes_fingerprint(monkeypatch):
    _, fp1 = await _fp(monkeypatch, [_parent("a.pdf", 40), _parent("s.pdf", 56)])
    _, fp2 = await _fp(monkeypatch, [_parent("a.pdf", 40), _parent("s.pdf", 58)])
    assert fp1 != fp2


async def test_add_plus_delete_same_count_changes_fingerprint(monkeypatch):
    # The trap a bare document COUNT would miss: still two documents.
    _, fp1 = await _fp(monkeypatch, [_parent("a.pdf", 40), _parent("s.pdf", 56)])
    _, fp2 = await _fp(monkeypatch, [_parent("a.pdf", 40), _parent("other.pdf", 56)])
    assert fp1 != fp2


async def test_fingerprint_stable_and_order_independent(monkeypatch):
    # Same set, different insertion order, repeated calls: identical hash --
    # no dict-ordering or timestamp leakage.
    ab = [_parent("a.pdf", 40), _parent("s.pdf", 56)]
    ba = [_parent("s.pdf", 56), _parent("a.pdf", 40)]
    _, fp1 = await _fp(monkeypatch, ab)
    _, fp2 = await _fp(monkeypatch, ba)
    _, fp3 = await _fp(monkeypatch, ab)
    assert fp1 == fp2 == fp3


async def test_chunks_do_not_affect_fingerprint_and_empty_set(monkeypatch):
    parents = [_parent("a.pdf", 40)]
    _, fp1 = await _fp(monkeypatch, parents)
    _, fp2 = await _fp(monkeypatch, parents, extra=[_chunk("a.pdf", i) for i in range(3)])
    assert fp1 == fp2  # chunk rows are not part of the parent-doc fingerprint

    has, fp = await _fp(monkeypatch, [])
    assert has is False and fp == ""
    has, fp = await rag_service.thread_document_inventory(None, UID)
    assert has is False and fp == ""


# --- key strings: only THREAD_DOC changes ------------------------------------
BASE = "user-1:what is x"


def test_kb_query_key_byte_identical():
    assert _mode_cache_key(BASE, "KB_QUERY", TID, "fp123") == "user-1:what is x:KB_QUERY"


def test_general_key_byte_identical():
    assert _mode_cache_key(BASE, "GENERAL", TID, "fp123") == "user-1:what is x:GENERAL"


def test_mixed_key_byte_identical():
    assert _mode_cache_key(BASE, "MIXED", TID, "fp123") == "user-1:what is x:MIXED"


def test_thread_doc_key_carries_thread_and_fingerprint():
    assert (
        _mode_cache_key(BASE, "THREAD_DOC", TID, "fp123")
        == "user-1:what is x:THREAD_DOC:thread-C:fp123"
    )
    # Defensive: empty fingerprint degrades to the pre-Phase-2 key shape.
    assert (
        _mode_cache_key(BASE, "THREAD_DOC", TID, "")
        == "user-1:what is x:THREAD_DOC:thread-C"
    )


# --- the assertion that matters: no stale scope note under caching -----------
async def test_cached_scope_note_never_names_stale_document_set(monkeypatch):
    """A THREAD_DOC answer is cached under the fingerprint of the set its scope
    note names. Mutate the set -> the read key differs -> the stale entry is
    unreachable; an unchanged set -> hit, and the note still matches."""
    cache = {}

    set1 = [_parent("a.pdf", 40), _parent("s.pdf", 56)]
    _, fp1 = await _fp(monkeypatch, set1)
    key1 = _mode_cache_key(BASE, "THREAD_DOC", TID, fp1)
    cache[key1] = "answer...\n\n_Searched 2 attached documents: a.pdf and s.pdf. ..._"

    # Unchanged set: recomputed key hits, and the note names the current set.
    _, fp_again = await _fp(monkeypatch, set1)
    assert _mode_cache_key(BASE, "THREAD_DOC", TID, fp_again) in cache

    # Upload a third document: recomputed key MUST miss the stale entry.
    _, fp2 = await _fp(monkeypatch, set1 + [_parent("new.pdf", 12)])
    key2 = _mode_cache_key(BASE, "THREAD_DOC", TID, fp2)
    assert key2 != key1
    assert key2 not in cache  # fresh generation required; stale note unservable

    # Delete one: again unreachable.
    _, fp3 = await _fp(monkeypatch, [_parent("a.pdf", 40)])
    assert _mode_cache_key(BASE, "THREAD_DOC", TID, fp3) not in cache


# --- carried item 5: scope-note flag matrix across transports ----------------
BODY = "The answer body streamed token by token."
NOTE = "_Searched 2 attached documents: a.pdf and b.pdf. This answer is grounded in a.pdf. No content relevant to this question was found above the confidence threshold in b.pdf._"


def _fake_user():
    return User(
        id="U", email="u@example.com", hashed_password="x",
        created_at=__import__("datetime").datetime(2026, 1, 1), role="user",
    )


@pytest.mark.parametrize("router_on", [True, False])
@pytest.mark.parametrize("streaming_on", [True, False])
async def test_scope_note_once_and_identical_across_transports(
    monkeypatch, router_on, streaming_on
):
    """The same THREAD_DOC turn must produce STRING-IDENTICAL bodies over the
    JSON and SSE transports, with the scope note present exactly once when the
    router is on and absent when it is off -- no duplication from the SSE
    reconciliation tail, no omission."""
    monkeypatch.setattr(config, "ROUTER_ENABLED", router_on)
    monkeypatch.setattr(config, "STREAMING_ENABLED", streaming_on)

    answer = f"{BODY}\n\n{NOTE}" if router_on else BODY

    async def fake_turn(payload, current_user, emit=None):
        # Mirrors the real contract: generation streams the BODY; the scope
        # note is appended server-side AFTER generation, so it reaches the
        # stream only via the reconciliation tail.
        if emit is not None:
            await emit(BODY)
        return RAGChatResponse(answer=answer, sources=[], no_high_confidence_sources=False)

    monkeypatch.setattr(chat_mod, "_run_chat_turn", fake_turn)
    payload = RAGChatRequest(query="q", history=[], threadId=TID)

    # JSON transport body (what POST /chat returns).
    json_body = (await fake_turn(payload, _fake_user())).answer

    # SSE transport body (what the browser reassembles from token events).
    import json as jsonlib
    streamed, events = "", []
    async for frame in chat_mod._sse_chat_turn(payload, _fake_user()):
        for record in frame.split("\n\n"):
            if "event: token" in record:
                for line in record.split("\n"):
                    if line.startswith("data: "):
                        streamed += jsonlib.loads(line[6:])["text"]
            elif record.startswith("event: "):
                events.append(record.split("\n")[0][7:])

    assert streamed == json_body == answer
    expected_notes = 1 if router_on else 0
    assert json_body.count("_Searched 2 attached documents") == expected_notes
    assert streamed.count("_Searched 2 attached documents") == expected_notes


# --- carried item 6: excluded documents named as searched-but-not-included ---
def test_excluded_document_rendered_distinctly():
    scope = {
        "searched": ["a.pdf", "b.pdf", "c.pdf"],
        "grounded": ["a.pdf", "b.pdf"],
        "no_relevant": [],
        "excluded": ["c.pdf"],
    }
    note = _thread_scope_note(scope)
    assert "c.pdf matched this question but was not included" in note
    assert "No content relevant" not in note  # excluded is NOT searched-and-empty
