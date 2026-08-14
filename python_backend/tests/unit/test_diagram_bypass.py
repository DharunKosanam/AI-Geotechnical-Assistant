"""Diagram reranker-threshold bypass in THREAD_DOC retrieval.

A diagram's flattened text is a short structured node/edge listing that the
prose-calibrated cross-encoder floors below even the permissive thread
threshold. Diagrams are whole-document objects, so chunks with
sourceType == "diagram" bypass the threshold (capped by
THREAD_DIAGRAM_BYPASS_MAX) while prose chunks run today's quota/threshold
path unchanged.

Covers:
  1. diagram-only thread, below threshold: chunk kept, low_confidence False
     (chat.py's all-low-confidence gate therefore does NOT take the
     thread-aware fallback), scope reports the bypass;
  2. mixed thread, prose passing: prose kept-set identical to a diagram-free
     control run; diagram appended; diagram absent from no_relevant;
  3. mixed thread, prose ALL below threshold: prose low-confidence fallback
     context preserved exactly, diagram grounds the answer;
  4. REQUIRED: a diagram scoring ABOVE the threshold is included exactly once
     (never double-counted) and prose slots behave as a prose-only run --
     the honest guarantee is "prose chunks are ranked among themselves",
     NOT byte-identity of the combined pool;
  5. cap: over THREAD_DIAGRAM_BYPASS_MAX, the best-scoring diagram chunks are
     kept (rerank order as tiebreak) and the omitted ones are reported;
  6. diagram-free parity: the scope dict gains NO new keys and retrieval
     output is unchanged (the existing thread-doc suites pin the values);
  7. scope note: bypass and omitted sentences, single-attachment surfacing
     (vision-sentence precedent), and no-diagram wording untouched.

Deterministic fakes as in test_thread_doc_scope; no models load.
"""

import math

import pytest

from app.core import config
from app.routers.chat import _thread_scope_note
from app.services import rag_service

pytestmark = pytest.mark.unit

TID = "thread-DB"
UID = "U"
DOC = "report.pdf"
DIAG = "flow-ab12cd.png"

_DIM = 384
_QVEC = [1.0] + [0.0] * (_DIM - 1)


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


def _chunk(cid, filename, cos, text, source_type=None):
    d = {
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
    if source_type:
        d["sourceType"] = source_type
    return d


def _install(monkeypatch, docs, rerank_table):
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _FakeEmbed())
    monkeypatch.setattr(rag_service, "RERANKER_ENABLED", True)
    monkeypatch.setattr(rag_service, "get_reranker", lambda: _FakeReranker(rerank_table))
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)


def _prose_corpus(scores):
    docs, table = [], {}
    for i, s in enumerate(scores):
        text = f"DOC chunk {i}"
        docs.append(_chunk(f"p{i}", DOC, 0.60 - 0.01 * i, text))
        table[text] = s
    return docs, table


def _diagram_doc(cid="d0", filename=DIAG, score=-11.5, cos=0.50):
    text = f"Diagram: {filename}\nNodes: A [rectangle]; B [diamond]"
    return _chunk(cid, filename, cos, text, source_type="diagram"), {text: score}


# --- 1. diagram-only thread, below threshold ---------------------------------
async def test_diagram_only_thread_bypasses_threshold(monkeypatch):
    ddoc, dtable = _diagram_doc(score=-11.5)  # below the -11.0 thread threshold
    _install(monkeypatch, [ddoc], dtable)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    assert [c["id"] for c in chunks] == ["d0"]
    assert chunks[0]["low_confidence"] is False
    # chat.py's fallback gate is all(low_confidence): the bypass defeats it.
    assert not all(c.get("low_confidence") for c in chunks)
    assert scope["diagram_bypass"] == [DIAG]
    assert scope["grounded"] == [DIAG]
    assert scope["no_relevant"] == []
    assert "diagram_omitted" not in scope


# --- 2. mixed thread, prose passing ------------------------------------------
async def test_mixed_thread_prose_kept_set_matches_control(monkeypatch):
    prose_docs, table = _prose_corpus([-9.0, -9.5, -12.0])  # two pass, one fails
    ddoc, dtable = _diagram_doc(score=-11.5)
    table.update(dtable)

    _install(monkeypatch, prose_docs + [ddoc], table)
    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    # Control: the SAME prose corpus with no diagram in the thread.
    _install(monkeypatch, prose_docs, {k: v for k, v in table.items() if "Diagram" not in k})
    control = await rag_service.query_thread_documents("q", TID, UID, scope_out={})

    prose_kept = [c["id"] for c in chunks if c.get("sourceType") != "diagram"]
    assert prose_kept == [c["id"] for c in control]
    assert [c["id"] for c in chunks if c.get("sourceType") == "diagram"] == ["d0"]
    # The diagram is neither "relevant" nor "searched and empty".
    assert DIAG not in scope["no_relevant"]
    assert scope["diagram_bypass"] == [DIAG]
    assert scope["grounded"][-1] == DIAG  # appended after prose, kept order


# --- 3. mixed thread, prose all below threshold ------------------------------
async def test_prose_low_confidence_fallback_preserved_diagram_grounds(monkeypatch):
    prose_docs, table = _prose_corpus([-11.2, -11.4, -12.0])  # all fail
    ddoc, dtable = _diagram_doc(score=-11.5)
    table.update(dtable)
    _install(monkeypatch, prose_docs + [ddoc], table)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    prose_kept = [c for c in chunks if c.get("sourceType") != "diagram"]
    diagram_kept = [c for c in chunks if c.get("sourceType") == "diagram"]
    # Prose keeps today's low-confidence fallback context (hidden from sources).
    assert prose_kept and all(c["low_confidence"] for c in prose_kept)
    assert [c["id"] for c in diagram_kept] == ["d0"]
    assert diagram_kept[0]["low_confidence"] is False
    # Grounded = the diagram only; the prose doc is honestly searched-and-empty.
    assert scope["grounded"] == [DIAG]
    assert scope["no_relevant"] == [DOC]


# --- 4. REQUIRED: diagram ABOVE the threshold --------------------------------
async def test_above_threshold_diagram_included_once_prose_ranked_alone(monkeypatch):
    # "the diagram is never in passing" is empirical, not structural: labels
    # can match the query. The guarantee is NOT byte-identity of the combined
    # pool -- it is that the diagram appears exactly once (partition is
    # disjoint, so double-counting is impossible) and prose chunks are ranked
    # among THEMSELVES exactly as a prose-only run would rank them.
    prose_docs, table = _prose_corpus([-9.0, -9.5, -10.0])
    ddoc, dtable = _diagram_doc(score=-5.0)  # clears -11.0 comfortably
    table.update(dtable)
    _install(monkeypatch, prose_docs + [ddoc], table)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    assert [c["id"] for c in chunks].count("d0") == 1  # included exactly once
    # Prose slots behave as a prose-only run.
    _install(monkeypatch, prose_docs, {k: v for k, v in table.items() if "Diagram" not in k})
    control = await rag_service.query_thread_documents("q", TID, UID, scope_out={})
    assert [c["id"] for c in chunks if c.get("sourceType") != "diagram"] == \
        [c["id"] for c in control]
    # Bookkeeping: bypass, not "passing" -- the score was never the reason.
    assert scope["diagram_bypass"] == [DIAG]
    assert DIAG not in scope["no_relevant"]
    assert DIAG not in scope["excluded"]


# --- 5. the cap ---------------------------------------------------------------
async def test_cap_keeps_best_scoring_diagrams_and_reports_omitted(monkeypatch):
    docs, table = [], {}
    # Five diagrams with distinct rerank scores, deliberately out of order.
    scores = {"a.png": -11.9, "b.png": -11.1, "c.png": -12.5, "d.png": -11.3, "e.png": -11.7}
    for i, (fn, s) in enumerate(scores.items()):
        d, t = _diagram_doc(cid=f"dg{i}", filename=fn, score=s, cos=0.50 - 0.01 * i)
        docs.append(d)
        table.update(t)
    _install(monkeypatch, docs, table)
    monkeypatch.setattr(config, "THREAD_DIAGRAM_BYPASS_MAX", 3)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    kept = {c["filename"] for c in chunks if c.get("sourceType") == "diagram"}
    assert kept == {"b.png", "d.png", "e.png"}  # top 3 by rerank score
    assert scope["diagram_bypass"] == sorted(kept)
    assert scope["diagram_omitted"] == ["a.png", "c.png"]
    assert all(fn not in scope["no_relevant"] for fn in scores)


# --- 6. diagram-free parity ----------------------------------------------------
async def test_diagram_free_thread_scope_gains_no_new_keys(monkeypatch):
    prose_docs, table = _prose_corpus([-9.0, -9.5, -12.0])
    _install(monkeypatch, prose_docs, table)

    scope = {}
    chunks = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    # Exactly the pre-bypass scope shape: values are pinned by the existing
    # test_thread_doc_scope / test_thread_doc_quota suites.
    assert set(scope) == {"searched", "grounded", "no_relevant", "excluded", "vision"}
    assert {c["filename"] for c in chunks} == {DOC}
    note = _thread_scope_note(scope)
    assert "diagram" not in note


# --- 7. scope note wording ------------------------------------------------------
def _scope(**overrides):
    base = {
        "searched": [DOC, DIAG],
        "grounded": [DOC, DIAG],
        "no_relevant": [],
        "excluded": [],
        "pending": [],
        "failed": [],
        "diagram_files": [DIAG],
    }
    base.update(overrides)
    return base


def test_note_bypass_sentence_states_inclusion_mode_not_relevance():
    note = _thread_scope_note(_scope(diagram_bypass=[DIAG]))
    assert (
        f"{DIAG} is included in full: diagrams are read whole rather than "
        f"relevance-ranked." in note
    )
    # The relevance sentence is never claimed for the diagram.
    assert f"No content relevant to this question was found" not in note


def test_note_omitted_sentence_never_implies_the_diagram_was_read():
    note = _thread_scope_note(_scope(
        searched=[DOC, DIAG],
        grounded=[DOC, DIAG],
        diagram_files=[DIAG, "extra.png"],
        diagram_bypass=[DIAG],
        diagram_omitted=["extra.png"],
    ))
    assert "extra.png was not read for this answer (over the per-answer diagram limit)." in note
    # The omitted diagram appears ONLY in its own sentence -- never among the
    # searched or grounded names, which would imply it was consulted.
    before_bypass_sentence = note[: note.index("included in full")]
    assert "extra.png" not in before_bypass_sentence
    assert "grounded in" in before_bypass_sentence


def test_note_single_attachment_surfaces_bypass_like_vision():
    # total_attached < 2 normally renders no note; the bypass sentence follows
    # the vision-sentence precedent and surfaces anyway.
    note = _thread_scope_note(_scope(
        searched=[DIAG], grounded=[DIAG], diagram_bypass=[DIAG],
        diagram_files=[DIAG],
    ))
    assert note == (
        f"_{DIAG} is included in full: diagrams are read whole rather than "
        f"relevance-ranked._"
    )
