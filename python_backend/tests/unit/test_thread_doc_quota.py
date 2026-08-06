"""Phase 3 unit tests: per-document representation quotas in THREAD_DOC
retrieval.

Reproduces the production incident shape (thread 7162ec5f...): a 40-chunk older
document holding 19 of the 25 cosine candidate slots and all 5 post-rerank
context slots, while the 56-chunk newer document's best chunk sat at global
rerank rank ~7 -- so the answer cited the wrong file. The quota guarantees every
document in the thread is CONSIDERED at both stages; the -11.0 threshold still
decides INCLUSION.

Deterministic: fake files_collection, fake embedder (cosine controlled exactly
via 2-component unit vectors), fake cross-encoder (scores looked up by chunk
text). No models load.
"""

import math

import pytest

from app.core import config
from app.services import rag_service

pytestmark = pytest.mark.unit

TID = "thread-Q"
UID = "U"
OLD = "Bender element.pdf"
NEW = "s40515-014-0006-3.pdf"

_DIM = 384
_QVEC = [1.0] + [0.0] * (_DIM - 1)


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


class _Vec(list):
    def tolist(self):
        return list(self)


class _FakeEmbed:
    """Returns the fixed query vector for any embed() call. Chunk embeddings
    live on the docs themselves, so only the query goes through this."""

    def embed(self, texts):
        return [_Vec(_QVEC) for _ in texts]


class _FakeReranker:
    """Cross-encoder stand-in: rerank score looked up by exact chunk text."""

    def __init__(self, table):
        self.table = table

    def rerank(self, query, texts):
        return [self.table[t] for t in texts]


def _emb(cos):
    """A 384-dim unit vector whose cosine against _QVEC is exactly `cos`."""
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


def _filenames(chunks):
    return [c["filename"] for c in chunks]


# --- incident-shape corpus ---------------------------------------------------
def _incident_corpus():
    """40 OLD + 56 NEW chunks whose cosines put 19 OLD / 6 NEW into the top-25
    (the exact production composition) and whose rerank scores put OLD in all
    five top slots with NEW's best at global rank 7 -- mirroring the replay:
    OLD -9.15..-10.43 in the top five, NEW best -10.50 just past the cutoff."""
    docs, table = [], {}

    for i in range(40):  # OLD: cos 0.600 down in 0.004 steps
        text = f"OLD chunk {i} bender element shear wave velocity"
        docs.append(_chunk(f"old{i}", OLD, 0.600 - 0.004 * i, text))
        # top five mirror the incident scores; the rest slope away below them
        rr = [-9.15, -10.11, -10.31, -10.33, -10.43][i] if i < 5 else -10.55 - 0.01 * i
        table[text] = rr

    for j in range(56):  # NEW: cos 0.550 down in 0.004 steps -> 6 in top-25
        text = f"NEW chunk {j} geosynthetic reinforced limit state design"
        docs.append(_chunk(f"new{j}", NEW, 0.550 - 0.004 * j, text))
        table[text] = -10.50 - 0.05 * j  # best -10.50, all above -11.0 for j<10

    return docs, table


# --- the regression test (must FAIL on pre-quota code) -----------------------
async def test_incident_shape_new_document_reaches_context(monkeypatch):
    docs, table = _incident_corpus()
    _install(monkeypatch, docs, table, router_on=True)

    chunks = await rag_service.query_thread_documents("what is pinn in this pdf", TID, UID)

    names = _filenames(chunks)
    assert len(chunks) == 5
    # The newer document must contribute at least one chunk to the context.
    assert NEW in names, f"newer document crowded out of context: {names}"
    # And the older document is still represented (it holds the best scores).
    assert OLD in names


# --- single-document thread: byte-identical to current behavior --------------
async def test_single_document_thread_identical_to_plain_top_k(monkeypatch):
    docs, table = [], {}
    for i in range(12):
        text = f"solo chunk {i}"
        docs.append(_chunk(f"solo{i}", "only.pdf", 0.60 - 0.01 * i, text))
        table[text] = -9.0 - 0.1 * i  # all pass threshold, strictly ordered
    _install(monkeypatch, docs, table, router_on=True)

    chunks = await rag_service.query_thread_documents("q", TID, UID)

    # Exactly the plain top-5 by rerank score, in rerank order, low_confidence
    # False -- indistinguishable from the pre-quota pipeline.
    assert [c["id"] for c in chunks] == ["solo0", "solo1", "solo2", "solo3", "solo4"]
    assert all(c["low_confidence"] is False for c in chunks)


# --- flag off: pre-quota behavior preserved exactly (gating proof) -----------
async def test_router_flag_off_keeps_old_crowding_behavior(monkeypatch):
    docs, table = _incident_corpus()
    _install(monkeypatch, docs, table, router_on=False)

    chunks = await rag_service.query_thread_documents("what is pinn in this pdf", TID, UID)

    # Old behavior: the five OLD chunks monopolise the context.
    assert _filenames(chunks) == [OLD] * 5


# --- more documents than context slots ---------------------------------------
async def test_more_documents_than_slots_one_each_best_first(monkeypatch, capsys):
    docs, table = [], {}
    # 7 documents, 2 chunks each, ALL above the -11.0 threshold (best scores
    # -9.0 .. -10.2), so every document qualifies and there are more qualifying
    # documents than the 5 context slots. Best-doc order is d0 > d1 > ... > d6.
    for k in range(7):
        for i in range(2):
            text = f"doc{k} chunk {i}"
            docs.append(_chunk(f"d{k}c{i}", f"doc{k}.pdf", 0.6 - 0.01 * k - 0.001 * i, text))
            table[text] = -9.0 - 0.2 * k - 0.05 * i
    _install(monkeypatch, docs, table, router_on=True)

    chunks = await rag_service.query_thread_documents("q", TID, UID)

    assert len(chunks) == 5
    # One chunk per document, docs chosen in best-score order d0..d4.
    assert _filenames(chunks) == [f"doc{k}.pdf" for k in range(5)]
    assert [c["id"] for c in chunks] == [f"d{k}c0" for k in range(5)]
    # The exclusion of d5/d6 is logged.
    out = capsys.readouterr().out
    assert "excluded" in out and "doc5.pdf" in out and "doc6.pdf" in out


# --- below-threshold document contributes nothing ----------------------------
async def test_below_threshold_document_does_not_displace(monkeypatch):
    docs, table = [], {}
    for i in range(6):  # doc A: all pass
        text = f"passing chunk {i}"
        docs.append(_chunk(f"a{i}", "passing.pdf", 0.60 - 0.01 * i, text))
        table[text] = -9.0 - 0.1 * i
    for i in range(3):  # doc B: every chunk below -11.0
        text = f"failing chunk {i}"
        docs.append(_chunk(f"b{i}", "failing.pdf", 0.59 - 0.01 * i, text))
        table[text] = -11.5 - 0.5 * i
    _install(monkeypatch, docs, table, router_on=True)

    chunks = await rag_service.query_thread_documents("q", TID, UID)

    # Quota guarantees consideration, not inclusion: B contributes zero and A's
    # fifth-best chunk is NOT displaced by a reserved-but-failing B chunk.
    assert _filenames(chunks) == ["passing.pdf"] * 5
    assert [c["id"] for c in chunks] == ["a0", "a1", "a2", "a3", "a4"]


# --- nothing passes anywhere: low-confidence fallback unchanged --------------
async def test_all_below_threshold_low_confidence_fallback(monkeypatch):
    docs, table = [], {}
    for k in range(2):
        for i in range(4):
            text = f"weak doc{k} chunk {i}"
            docs.append(_chunk(f"w{k}c{i}", f"weak{k}.pdf", 0.6 - 0.01 * i, text))
            table[text] = -11.2 - 0.1 * i - k
    _install(monkeypatch, docs, table, router_on=True)

    chunks = await rag_service.query_thread_documents("q", TID, UID)

    # Same low-confidence fallback as the pre-quota path: a small context set,
    # every chunk tagged low_confidence so no sources are displayed.
    assert chunks, "fallback must still hand the LLM some context"
    assert all(c["low_confidence"] is True for c in chunks)
