"""Phase 4 unit tests: multi-document scope and citation honesty.

The scope statement must derive from the ACTUAL retrieval result (scope_out
filled by query_thread_documents from the same data the Phase 3 quota ran on),
and the deterministic note assembled by chat._thread_scope_note must name
searched-but-empty documents instead of omitting them. Single-document threads
produce no note and identical retrieval output.

Deterministic fakes as in test_thread_doc_quota; no models load. The live
incident replay (read-only, run at the phase boundary) is the end-to-end
acceptance check; these tests pin the components it relies on.
"""

import math

import pytest

from app.core import config
from app.routers.chat import _thread_scope_note
from app.services import rag_service

pytestmark = pytest.mark.unit

TID = "thread-S"
UID = "U"
OLD = "Bender element.pdf"
NEW = "s40515-014-0006-3.pdf"

_DIM = 384
_QVEC = [1.0] + [0.0] * (_DIM - 1)


# --- fakes (as in test_thread_doc_quota) -------------------------------------
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


class _Vec(list):
    def tolist(self):
        return list(self)


class _FakeEmbed:
    def embed(self, texts):
        return [_Vec(_QVEC) for _ in texts]


class _FakeReranker:
    def __init__(self, table):
        self.table = table

    def rerank(self, query, texts):
        return [self.table[t] for t in texts]


def _emb(cos):
    return [cos, math.sqrt(max(0.0, 1.0 - cos * cos))] + [0.0] * (_DIM - 2)


def _chunk(cid, filename, cos, text):
    return {
        "_id": cid,
        "category": "thread_upload",
        "chunkIndex": 0,
        "filename": filename,
        "text": text,
        "embedding": _emb(cos),
        "metadata": {},
        "threadId": TID,
        "userId": UID,
    }


def _install(monkeypatch, docs, rerank_table, router_on=True):
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _FakeEmbed())
    monkeypatch.setattr(rag_service, "RERANKER_ENABLED", True)
    monkeypatch.setattr(rag_service, "get_reranker", lambda: _FakeReranker(rerank_table))
    monkeypatch.setattr(config, "ROUTER_ENABLED", router_on)


def _two_doc_corpus(new_scores):
    """OLD with five passing chunks; NEW with three chunks scored per
    ``new_scores`` (lets each test choose whether NEW passes -11.0)."""
    docs, table = [], {}
    for i in range(5):
        text = f"OLD chunk {i}"
        docs.append(_chunk(f"o{i}", OLD, 0.60 - 0.01 * i, text))
        table[text] = -9.0 - 0.2 * i
    for j, s in enumerate(new_scores):
        text = f"NEW chunk {j}"
        docs.append(_chunk(f"n{j}", NEW, 0.58 - 0.01 * j, text))
        table[text] = s
    return docs, table


# --- scope_out: searched-but-empty document is recorded, not omitted ---------
async def test_scope_records_searched_but_empty_document(monkeypatch):
    docs, table = _two_doc_corpus(new_scores=[-11.2, -11.4, -12.0])  # NEW all fail
    _install(monkeypatch, docs, table)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    assert scope["searched"] == sorted([OLD, NEW])
    assert scope["grounded"] == [OLD]          # only the contributing document
    assert scope["no_relevant"] == [NEW]       # searched, nothing above -11.0
    # ...and the grounding (sources are built from these chunks) is OLD-only.
    assert {c["filename"] for c in chunks} == {OLD}

    note = _thread_scope_note(scope)
    assert "Searched 2 attached documents" in note
    assert NEW in note and OLD in note
    assert f"grounded in {OLD}" in note
    assert "No content relevant to this question was found above the confidence threshold in " + NEW in note


# --- the live incident composition (rewritten-style PINN query) --------------
async def test_incident_composition_names_new_file_as_searched(monkeypatch):
    # Real replay numbers: OLD's best chunk -10.63 (passes), every NEW chunk
    # below -11.0 (best -11.14). Correct retrieval; the note must make it honest.
    docs, table = _two_doc_corpus(new_scores=[-11.14, -11.16, -11.19])
    for i in range(5):
        table[f"OLD chunk {i}"] = [-10.63, -11.04, -11.12, -11.13, -11.13][i]
    _install(monkeypatch, docs, table)

    scope = {}
    chunks = await rag_service.query_thread_documents(
        "What is PINN (Physics-Informed Neural Network)?", TID, UID, scope_out=scope
    )

    assert scope["grounded"] == [OLD]
    assert scope["no_relevant"] == [NEW]

    note = _thread_scope_note(scope)
    # Names the new file as searched...
    assert NEW in note
    # ...says nothing relevant was found in it (so the answer cannot read as
    # "PINN was found")...
    assert "No content relevant to this question was found above the confidence threshold in " + NEW in note
    # ...and cannot imply the old paper was "the attached document": the note
    # states there were two.
    assert "Searched 2 attached documents" in note


# --- single-document thread: identity, and no scope statement ----------------
async def test_single_document_no_note_and_identical_chunks(monkeypatch):
    docs, table = [], {}
    for i in range(8):
        text = f"solo chunk {i}"
        docs.append(_chunk(f"s{i}", "only.pdf", 0.6 - 0.01 * i, text))
        table[text] = -9.0 - 0.1 * i
    _install(monkeypatch, docs, table)

    scope = {}
    with_scope = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)
    without = await rag_service.query_thread_documents("q", TID, UID)

    # Passing scope_out changes nothing about retrieval output.
    assert [c["id"] for c in with_scope] == [c["id"] for c in without]
    # And a single-document thread emits NO scope statement.
    assert scope["searched"] == ["only.pdf"]
    assert _thread_scope_note(scope) == ""


# --- both documents contribute: both named in the grounding ------------------
async def test_both_documents_grounded_both_named(monkeypatch):
    docs, table = _two_doc_corpus(new_scores=[-9.5, -10.0, -10.5])  # NEW passes
    _install(monkeypatch, docs, table)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    assert set(scope["grounded"]) == {OLD, NEW}
    assert scope["no_relevant"] == []
    assert {c["filename"] for c in chunks} == {OLD, NEW}

    note = _thread_scope_note(scope)
    assert "Searched 2 attached documents" in note
    assert "grounded in" in note and OLD in note and NEW in note
    assert "No content relevant" not in note


# --- nothing anywhere clears the threshold -----------------------------------
async def test_nothing_passes_note_covers_all_documents(monkeypatch):
    docs, table = _two_doc_corpus(new_scores=[-11.2, -11.3, -11.4])
    for i in range(5):
        table[f"OLD chunk {i}"] = -11.5 - 0.1 * i  # OLD fails too
    _install(monkeypatch, docs, table)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    # Low-confidence fallback context grounds nothing.
    assert all(c["low_confidence"] for c in chunks)
    assert scope["grounded"] == []
    note = _thread_scope_note(scope)
    assert "in any of them" in note
    assert "grounded in" not in note


# --- flag off: no scope machinery, no note (byte-identity guard) -------------
async def test_router_flag_off_no_note(monkeypatch):
    docs, table = _two_doc_corpus(new_scores=[-11.2, -11.4, -12.0])
    _install(monkeypatch, docs, table, router_on=False)

    scope = {}
    await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    # Flag off fills only `searched`; the note builder refuses to speak without
    # the full picture, so flag-off answer text is untouched.
    assert "grounded" not in scope
    assert _thread_scope_note(scope) == ""
