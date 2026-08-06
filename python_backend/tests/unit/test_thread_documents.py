"""Phase 7/8 Part A unit tests: per-thread document storage + thread-scoped
retrieval, with the cross-thread isolation guarantee at the center.

Deterministic: a fake files_collection applies the query filter, a fake embedder
avoids loading any model, and the cross-encoder reranker is disabled so ranking
uses the plain vector floor. The point under test is ISOLATION (which rows are
eligible), which is enforced by the Mongo filter regardless of ranking.
"""

from types import SimpleNamespace

import pytest

from app.services import rag_service

pytestmark = pytest.mark.unit


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
        self._i = 0

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
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted = []

    def find(self, flt, projection=None):
        return _AsyncIter([d for d in self.docs if _matches(d, flt)])

    async def find_one(self, flt, projection=None):
        for d in self.docs:
            if _matches(d, flt):
                return d
        return None

    async def insert_many(self, docs):
        self.inserted.extend(docs)
        self.docs.extend(docs)
        return SimpleNamespace(inserted_ids=list(range(len(docs))))


class _Vec(list):
    def tolist(self):
        return list(self)


class _FakeEmbed:
    def __init__(self, vec):
        self.vec = vec

    def embed(self, texts):
        return [_Vec(self.vec) for _ in texts]


_DIM = 384
_EVEC = [1.0] + [0.0] * (_DIM - 1)
# Deliberately near-identical text across threads / the shared index, so the
# isolation test proves separation is by SCOPE, not by text dissimilarity.
NEAR = "Standard Proctor compaction optimum moisture content maximum dry density"


def _chunk(_id, category, thread=None, user="U"):
    d = {
        "_id": _id,
        "category": category,
        "chunkIndex": 0,
        "filename": f"{_id}.pdf",
        "text": NEAR,
        "embedding": list(_EVEC),
        "metadata": {},
    }
    if thread is not None:
        d["threadId"] = thread
    if user is not None:
        d["userId"] = user
    return d


def _corpus():
    return [
        _chunk("a1", "thread_upload", thread="thread-A"),          # thread A
        _chunk("b1", "thread_upload", thread="thread-B"),          # thread B
        _chunk("kb1", "knowledge_base", user=None),                # shared index
        _chunk("u1", "user_upload"),                               # plain user upload
    ]


@pytest.fixture
def thread_env(monkeypatch):
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(_corpus()))
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _FakeEmbed(_EVEC))
    # Disable the cross-encoder so no model loads; isolation is filter-driven and
    # independent of the reranker.
    monkeypatch.setattr(rag_service, "RERANKER_ENABLED", False)


# --- scope filter -----------------------------------------------------------
def test_thread_scope_filter_requires_category_thread_user_and_chunk():
    f = rag_service._thread_scope_filter("U", "thread-B")
    assert f["category"] == "thread_upload"
    assert f["threadId"] == "thread-B"
    assert f["userId"] == "U"
    assert f["chunkIndex"] == {"$exists": True}


def test_thread_scope_filter_omits_user_when_absent():
    f = rag_service._thread_scope_filter(None, "thread-B")
    assert "userId" not in f
    assert f["category"] == "thread_upload" and f["threadId"] == "thread-B"


# --- CROSS-THREAD ISOLATION (the centerpiece) -------------------------------
@pytest.mark.asyncio
async def test_thread_query_returns_only_that_thread(thread_env):
    results = await rag_service.query_thread_documents(NEAR, "thread-B", "U")
    ids = {r["id"] for r in results}
    assert ids == {"b1"}
    # NEGATIVE CASE: thread A holds a near-identical document, yet a thread-B
    # query never returns it -- separation is by scope, not text similarity.
    assert "a1" not in ids
    # never the shared knowledge base ...
    assert "kb1" not in ids
    # ... and never a plain (non-thread) user upload.
    assert "u1" not in ids


@pytest.mark.asyncio
async def test_thread_a_query_returns_only_thread_a(thread_env):
    results = await rag_service.query_thread_documents(NEAR, "thread-A", "U")
    assert {r["id"] for r in results} == {"a1"}


@pytest.mark.asyncio
async def test_unknown_thread_returns_empty(thread_env):
    assert await rag_service.query_thread_documents(NEAR, "thread-Z", "U") == []


@pytest.mark.asyncio
async def test_thread_query_scoped_to_user(thread_env):
    # thread-B exists but belongs to user "U"; another user must not retrieve it.
    assert await rag_service.query_thread_documents(NEAR, "thread-B", "OTHER") == []


# --- thread_has_documents ---------------------------------------------------
@pytest.mark.asyncio
async def test_thread_has_documents_scoping(monkeypatch):
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(_corpus()))
    assert await rag_service.thread_has_documents("thread-B", "U") is True
    assert await rag_service.thread_has_documents("thread-A", "U") is True
    assert await rag_service.thread_has_documents("thread-Z", "U") is False
    assert await rag_service.thread_has_documents("thread-B", "OTHER") is False
    assert await rag_service.thread_has_documents(None, "U") is False


# --- storage tagging --------------------------------------------------------
def _stub_ingest_deps(monkeypatch, fake_files):
    import app.services.file_processing as fp

    # `stats` is the optional coverage channel the real extractor fills in
    # (total/unreadable pages) so ingest can warn about a partially-read file.
    monkeypatch.setattr(
        fp,
        "extract_pages_from_file",
        lambda content, fn, stats=None: [(1, "hello world " * 30, False)],
    )
    monkeypatch.setattr(fp, "get_file_type", lambda fn: "pdf")
    monkeypatch.setattr(fp, "is_supported_file", lambda fn: True)
    monkeypatch.setattr(fp, "SUPPORTED_EXTENSIONS", {".pdf"})
    monkeypatch.setattr(
        rag_service,
        "chunk_text_v2",
        lambda pages: [{"text": "hello", "page_start": 1, "section_header": None, "chunk_index": 0}],
    )
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _FakeEmbed(_EVEC))
    monkeypatch.setattr(rag_service, "files_collection", fake_files)


@pytest.mark.asyncio
async def test_ingest_tags_thread_documents(monkeypatch):
    fake = _FakeFiles()
    _stub_ingest_deps(monkeypatch, fake)
    await rag_service.ingest_document(
        "r.pdf", b"xxx", category="thread_upload", user_id="U", thread_id="thread-B"
    )
    assert fake.inserted
    for d in fake.inserted:
        assert d["category"] == "thread_upload"
        assert d["threadId"] == "thread-B"
        assert d["metadata"]["threadId"] == "thread-B"
        assert d["userId"] == "U"


@pytest.mark.asyncio
async def test_ingest_without_thread_id_omits_threadid(monkeypatch):
    fake = _FakeFiles()
    _stub_ingest_deps(monkeypatch, fake)
    await rag_service.ingest_document("r.pdf", b"xxx", category="user_upload", user_id="U")
    assert fake.inserted
    for d in fake.inserted:
        assert "threadId" not in d
        assert "threadId" not in d["metadata"]
        assert d["category"] == "user_upload"


# --- Permissive thread-scoped reranker threshold (P1 fix) --------------------
class _FakeReranker:
    """Returns a fixed cross-encoder score for every candidate."""

    def __init__(self, score):
        self.score = score

    def rerank(self, query, docs):
        return [self.score for _ in docs]


def _thread_rerank_env(monkeypatch, rerank_score, thread_threshold=-11.0):
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles([_chunk("t1", "thread_upload", thread="T")]))
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _FakeEmbed(_EVEC))
    monkeypatch.setattr(rag_service, "RERANKER_ENABLED", True)
    monkeypatch.setattr(rag_service, "get_reranker", lambda: _FakeReranker(rerank_score))
    monkeypatch.setattr(rag_service, "THREAD_RERANK_SCORE_THRESHOLD", thread_threshold)


@pytest.mark.asyncio
async def test_thread_keeps_chunk_below_kb_threshold_but_above_thread_threshold(monkeypatch):
    # A chunk scoring -8.0 is BELOW the KB threshold (0.0) -- the exact case that
    # made on-target questions fall through -- but ABOVE the permissive thread
    # threshold (-11.0), so thread retrieval now KEEPS it as high-confidence
    # (chat.py will generate a THREAD_DOC answer and cite the doc).
    _thread_rerank_env(monkeypatch, rerank_score=-8.0, thread_threshold=-11.0)
    res = await rag_service.query_thread_documents("what did it find?", "T", "U")
    assert len(res) == 1
    assert res[0]["low_confidence"] is False   # kept -> on-target retrieves


@pytest.mark.asyncio
async def test_thread_falls_through_when_below_thread_threshold(monkeypatch):
    # A genuinely off-topic query scores at the cross-encoder floor (-12.0), below
    # even the permissive thread threshold -> tagged low_confidence, so chat.py's
    # retrieval-confidence fallback (thread-aware) still fires.
    _thread_rerank_env(monkeypatch, rerank_score=-12.0, thread_threshold=-11.0)
    res = await rag_service.query_thread_documents("unrelated off-topic question", "T", "U")
    assert res  # a low-confidence context chunk is returned...
    assert all(r["low_confidence"] for r in res)  # ...but NONE high-confidence -> falls through


def test_kb_threshold_unchanged_drops_the_same_negative_score():
    # Proves the KB path is NOT affected: the same -8.0 chunk that thread
    # retrieval keeps would be DROPPED (no high-confidence) by the KB threshold
    # (0.0), which _apply_rerank_threshold still uses by default.
    ch = [{"rerank_score": -8.0, "filename": "x.pdf", "text": "t"}]
    kept, no_high = rag_service._apply_rerank_threshold(ch)  # default KB threshold 0.0
    assert no_high is True
    assert all(c["low_confidence"] for c in kept)


def test_thread_threshold_is_separate_and_more_permissive_than_kb():
    from app.core import config
    # Separate constant, strictly more permissive (lower) than the KB threshold,
    # and the KB threshold itself is unchanged at 0.0.
    assert config.THREAD_RERANK_SCORE_THRESHOLD < config.RERANK_SCORE_THRESHOLD
    assert config.RERANK_SCORE_THRESHOLD == 0.0
    assert config.THREAD_RERANK_SCORE_THRESHOLD == -11.0  # documented default
