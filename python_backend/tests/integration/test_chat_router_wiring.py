"""Phase 4 wiring tests: the intent router dispatch inside POST /chat.

Drives the real endpoint through the ASGI app with auth overridden and every
heavy dependency (Redis, Mongo, retrieval, rewrite, LLM) mocked, so we can
assert the DISPATCH behavior precisely:

  * ROUTER_ENABLED off  -> classify() is never called; the KB retrieval pipeline
    runs exactly as before (flag-off parity).
  * ROUTER_ENABLED on, GENERAL -> no rewrite, no retrieval, no citations.
  * ROUTER_ENABLED on, KB_QUERY -> retrieval runs, sources returned.
  * ROUTER_ENABLED on, MIXED / THREAD_DOC -> fall back to KB_QUERY (retrieval
    runs, general handler untouched).
"""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from app.core import config
from app.core.rate_limit import limiter, rate_limit_identify
from app.main import app
from app.services.mode_handlers import ModeResult
from models import User
import app.routers.chat as chat_mod

pytestmark = pytest.mark.integration


# --- lightweight fakes ------------------------------------------------------
class _FakeCursor:
    """Async-iterable cursor supporting .sort().limit() then `async for`."""

    def __init__(self, docs):
        self._docs = list(docs)
        self._i = 0

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
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


class _FakeMessages:
    def __init__(self):
        self.inserted = []

    def find(self, *a, **k):
        return _FakeCursor([])  # no prior history

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="msg_id")


class _DictRedis:
    """Stateful fake cache that records every key read/written."""

    def __init__(self):
        self.store = {}
        self.get_keys = []
        self.set_keys = []

    async def get_cached_answer(self, key):
        self.get_keys.append(key)
        return self.store.get(key)

    async def set_cached_answer(self, key, answer, sources=None, ttl=None):
        self.set_keys.append(key)
        self.store[key] = {"answer": answer, "sources": sources or []}


_KB_CHUNK = {
    "filename": "StrengthanddilatancyofsandsBolton1986discussion1987.pdf",
    "text": "Sand dilatancy relates peak friction angle to relative density.",
    "metadata": {"fileType": ".pdf"},
    "low_confidence": False,
}
_KB_CHUNK2 = {
    "filename": "Critical-State-Of-Soil-Mechanics-Schofield-Wroth.pdf",
    "text": "Critical state theory relates void ratio to mean effective stress.",
    "metadata": {"fileType": ".pdf"},
    "low_confidence": False,
}
_THREAD_CHUNK = {
    "filename": "uploaded_report.pdf",
    "text": "Findings from the report the user uploaded into this thread.",
    "metadata": {"fileType": ".pdf"},
    "low_confidence": False,
}


@pytest_asyncio.fixture
async def chat_env(monkeypatch):
    """Patch every heavy dependency of POST /chat and yield the spies."""
    # Bypass auth: return a fixed user for the /chat dependency.
    fake_user = User(id="user-1", email="u@example.com", hashed_password="x")
    app.dependency_overrides[rate_limit_identify] = lambda: fake_user
    # Disable rate limiting so the decorator never touches Redis.
    monkeypatch.setattr(limiter, "enabled", False)

    # Redis + Mongo messages
    fake_redis = _DictRedis()
    monkeypatch.setattr(chat_mod, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(chat_mod, "messages_collection", _FakeMessages())

    # Pipeline pieces (spies)
    classify_mock = AsyncMock(return_value=chat_mod.KB_QUERY)
    rewrite_mock = AsyncMock(return_value="bolton dilatancy")
    retrieve_mock = AsyncMock(return_value=[dict(_KB_CHUNK)])
    generate_mock = AsyncMock(return_value="A KB-grounded answer about dilatancy.")
    general_mock = AsyncMock(
        return_value=ModeResult(answer="A general answer.", sources=[], no_high_confidence_sources=False)
    )
    thread_fallback_mock = AsyncMock(
        return_value=ModeResult(
            answer="I searched your uploaded document but couldn't find that. In general, ...",
            sources=[],
            no_high_confidence_sources=False,
        )
    )
    thread_has_docs_mock = AsyncMock(return_value=False)
    thread_retrieve_mock = AsyncMock(return_value=[dict(_THREAD_CHUNK)])
    monkeypatch.setattr(chat_mod, "classify", classify_mock)
    monkeypatch.setattr(chat_mod, "rewrite_query_with_history", rewrite_mock)
    monkeypatch.setattr(chat_mod, "query_vector_store", retrieve_mock)
    monkeypatch.setattr(chat_mod, "query_thread_documents", thread_retrieve_mock)
    monkeypatch.setattr(chat_mod, "thread_has_documents", thread_has_docs_mock)
    monkeypatch.setattr(chat_mod, "generate_answer_with_groq", generate_mock)
    monkeypatch.setattr(chat_mod, "handle_general", general_mock)
    monkeypatch.setattr(chat_mod, "handle_thread_doc_fallback", thread_fallback_mock)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(
            client=client,
            classify=classify_mock,
            rewrite=rewrite_mock,
            retrieve=retrieve_mock,
            generate=generate_mock,
            general=general_mock,
            thread_fallback=thread_fallback_mock,
            thread_has_docs=thread_has_docs_mock,
            thread_retrieve=thread_retrieve_mock,
            redis=fake_redis,
        )

    app.dependency_overrides.pop(rate_limit_identify, None)


_QUERY = "Tell me about sand dilatancy"
_BASE_KEY = f"user-1:{_QUERY}"


async def _post(client):
    return await client.post("/chat", json={"query": _QUERY, "threadId": "t1"})


# --- flag OFF: parity with pre-router behavior ------------------------------
@pytest.mark.asyncio
async def test_flag_off_never_calls_router_and_runs_kb_pipeline(chat_env, monkeypatch):
    monkeypatch.setattr(config, "ROUTER_ENABLED", False)
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.classify.assert_not_awaited()          # router never consulted
    chat_env.retrieve.assert_awaited_once()          # retrieval ran
    chat_env.general.assert_not_awaited()            # general handler untouched
    body = resp.json()
    assert body["answer"] == "A KB-grounded answer about dilatancy."
    assert len(body["sources"]) == 1                 # KB citation present
    # KB path drives the LLM in KB_QUERY mode
    assert chat_env.generate.await_args.kwargs["mode"] == chat_mod.KB_QUERY


# --- flag ON: GENERAL -------------------------------------------------------
@pytest.mark.asyncio
async def test_flag_on_general_skips_retrieval_and_returns_no_sources(chat_env, monkeypatch):
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.GENERAL
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.classify.assert_awaited_once()
    chat_env.rewrite.assert_not_awaited()            # GENERAL skips rewrite
    chat_env.retrieve.assert_not_awaited()           # GENERAL skips retrieval
    chat_env.general.assert_awaited_once()
    body = resp.json()
    assert body["answer"] == "A general answer."
    assert body["sources"] == []                     # NO citations
    assert body["no_high_confidence_sources"] is False


# --- flag ON: KB_QUERY ------------------------------------------------------
@pytest.mark.asyncio
async def test_flag_on_kb_query_runs_retrieval_with_citations(chat_env, monkeypatch):
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.KB_QUERY
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.classify.assert_awaited_once()
    chat_env.retrieve.assert_awaited_once()
    chat_env.general.assert_not_awaited()
    body = resp.json()
    assert len(body["sources"]) == 1


# --- cache key: mode scoping ------------------------------------------------
@pytest.mark.asyncio
async def test_flag_off_cache_key_is_plain_base(chat_env, monkeypatch):
    # Flag OFF: both the read and the write use the base key, byte-identical to
    # the pre-router key format (no mode suffix).
    monkeypatch.setattr(config, "ROUTER_ENABLED", False)
    await _post(chat_env.client)
    assert chat_env.redis.get_keys == [_BASE_KEY]
    assert chat_env.redis.set_keys == [_BASE_KEY]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["KB_QUERY", "GENERAL"])
async def test_flag_on_cache_key_includes_mode(chat_env, monkeypatch, mode):
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = mode
    await _post(chat_env.client)
    expected = f"{_BASE_KEY}:{mode}"
    assert chat_env.redis.get_keys == [expected]
    assert chat_env.redis.set_keys == [expected]


@pytest.mark.asyncio
async def test_kb_cached_answer_not_served_for_general_turn(chat_env, monkeypatch):
    # A prior KB_QUERY answer sits in the cache under the KB-scoped key.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.redis.store[f"{_BASE_KEY}:KB_QUERY"] = {
        "answer": "STALE KB ANSWER WITH SOURCES",
        "sources": [{"title": "Bolton (1986)"}],
    }
    # Same query now classifies as GENERAL -> must MISS the KB entry and answer
    # generally, never serving the stale KB answer or its sources.
    chat_env.classify.return_value = chat_mod.GENERAL
    resp = await _post(chat_env.client)
    body = resp.json()
    assert body["answer"] == "A general answer."          # not the stale KB text
    assert body["sources"] == []                           # not the stale sources
    chat_env.general.assert_awaited_once()
    # The GENERAL turn read/wrote only the GENERAL-scoped key.
    assert chat_env.redis.get_keys == [f"{_BASE_KEY}:GENERAL"]
    assert chat_env.redis.set_keys == [f"{_BASE_KEY}:GENERAL"]


@pytest.mark.asyncio
async def test_kb_cached_answer_is_served_for_matching_kb_turn(chat_env, monkeypatch):
    # The same primed KB entry IS served when the turn classifies as KB_QUERY.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.redis.store[f"{_BASE_KEY}:KB_QUERY"] = {
        "answer": "CACHED KB ANSWER",
        "sources": [{"title": "Bolton (1986)"}],
    }
    chat_env.classify.return_value = chat_mod.KB_QUERY
    resp = await _post(chat_env.client)
    body = resp.json()
    assert body["answer"] == "CACHED KB ANSWER"            # cache hit
    assert body["sources"] == [{"title": "Bolton (1986)"}]
    chat_env.retrieve.assert_not_awaited()                 # short-circuited on hit
    chat_env.generate.assert_not_awaited()


# --- Phase 5: retrieval-confidence fallback ---------------------------------
_LOW_CONF_CHUNK = {
    "filename": "some.pdf",
    "text": "weakly related text",
    "metadata": {"fileType": ".pdf"},
    "low_confidence": True,
}


@pytest.mark.asyncio
async def test_kb_all_low_confidence_falls_through_to_general(chat_env, monkeypatch):
    # Retrieval SUCCEEDED but every chunk is below the reranker threshold ->
    # answer generally, no citations.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.KB_QUERY
    chat_env.retrieve.return_value = [dict(_LOW_CONF_CHUNK)]
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.retrieve.assert_awaited_once()
    chat_env.general.assert_awaited_once()        # fell through to GENERAL
    chat_env.generate.assert_not_awaited()        # KB generation skipped
    body = resp.json()
    assert body["answer"] == "A general answer."
    assert body["sources"] == []
    assert body["no_high_confidence_sources"] is False


@pytest.mark.asyncio
async def test_kb_empty_retrieval_falls_through_to_general(chat_env, monkeypatch):
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.KB_QUERY
    chat_env.retrieve.return_value = []
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.general.assert_awaited_once()
    chat_env.generate.assert_not_awaited()
    assert resp.json()["sources"] == []


@pytest.mark.asyncio
async def test_kb_high_confidence_does_not_fall_through(chat_env, monkeypatch):
    # At least one chunk clears the threshold -> normal KB answer, no fallback.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.KB_QUERY
    chat_env.retrieve.return_value = [dict(_KB_CHUNK)]  # low_confidence False
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.generate.assert_awaited_once()       # KB generation ran
    chat_env.general.assert_not_awaited()         # NO fallback
    assert len(resp.json()["sources"]) == 1


@pytest.mark.asyncio
async def test_retrieval_error_surfaces_as_500_not_general(chat_env, monkeypatch):
    # THE critical distinction: a retrieval OUTAGE must surface as an error, not
    # be silently converted into an uncited GENERAL answer.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.KB_QUERY
    chat_env.retrieve.side_effect = RuntimeError("mongo/reranker down")
    resp = await _post(chat_env.client)
    assert resp.status_code == 500
    chat_env.general.assert_not_awaited()         # NOT converted to GENERAL
    chat_env.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_off_low_confidence_does_not_fall_through(chat_env, monkeypatch):
    # Flag OFF: a low-confidence retrieval keeps today's behavior exactly -- the
    # KB pipeline still generates (from low-conf context) and reports
    # no_high_confidence_sources=True. No GENERAL fallback.
    monkeypatch.setattr(config, "ROUTER_ENABLED", False)
    chat_env.retrieve.return_value = [dict(_LOW_CONF_CHUNK)]
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.classify.assert_not_awaited()
    chat_env.general.assert_not_awaited()         # no fallback when flag off
    chat_env.generate.assert_awaited_once()       # KB generation ran as before
    body = resp.json()
    assert body["sources"] == []
    assert body["no_high_confidence_sources"] is True


# --- Phase 6: MIXED ---------------------------------------------------------
@pytest.mark.asyncio
async def test_mixed_uses_mixed_prompt_and_retrieves(chat_env, monkeypatch):
    # MIXED is live now: it retrieves AND generates with the MIXED prompt (not
    # forced to KB_QUERY, not treated as GENERAL).
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.MIXED
    resp = await _post(chat_env.client)  # default retrieve -> [_KB_CHUNK]
    assert resp.status_code == 200
    chat_env.retrieve.assert_awaited_once()
    chat_env.general.assert_not_awaited()
    assert chat_env.generate.await_args.kwargs["mode"] == chat_mod.MIXED
    assert len(resp.json()["sources"]) == 1


@pytest.mark.asyncio
async def test_mixed_sources_length_matches_retrieved_not_answer_claims(chat_env, monkeypatch):
    # THE Phase 6 guarantee: citations returned cover ONLY the retrieved chunks;
    # no citation is fabricated for model-knowledge claims. The sources array
    # length is a function of RETRIEVAL, never of how many things the answer
    # cites in prose.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.MIXED
    chat_env.retrieve.return_value = [dict(_KB_CHUNK), dict(_KB_CHUNK2)]  # 2 retrieved
    # The answer fabricates several citations for its own-knowledge claims and
    # cites papers that were NOT retrieved.
    chat_env.generate.return_value = (
        "In general practice and according to Fabricated (2099), Made-Up (2100), "
        "and Invented (2101), soils behave a certain way. The lab data agrees."
    )
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    assert chat_env.generate.await_args.kwargs["mode"] == chat_mod.MIXED
    chat_env.general.assert_not_awaited()

    sources = resp.json()["sources"]
    # length == number of retrieved chunks (2), NOT the 3+ citations in the prose
    assert len(sources) == 2
    titles = {s["title"] for s in sources}
    # exactly the two retrieved chunk titles ...
    assert titles == {
        "Bolton (1986) - Strength and Dilatancy of Sands",
        "Schofield & Wroth - Critical State Soil Mechanics",
    }
    # ... and none of the fabricated model-knowledge citations leaked in
    assert all(
        not any(tok in t for tok in ("2099", "2100", "2101", "Fabricated", "Made-Up", "Invented"))
        for t in titles
    )


@pytest.mark.asyncio
async def test_mixed_sources_unchanged_even_if_answer_cites_nothing(chat_env, monkeypatch):
    # If the MIXED answer is pure model knowledge that cites none of the retrieved
    # docs, the retrieved chunks are STILL the citation set (sources come from
    # retrieval, not from parsing the answer). Proves no answer-driven narrowing.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.MIXED
    chat_env.retrieve.return_value = [dict(_KB_CHUNK), dict(_KB_CHUNK2)]
    chat_env.generate.return_value = "A general statement with no source references at all."
    resp = await _post(chat_env.client)
    assert len(resp.json()["sources"]) == 2  # == retrieved, not narrowed to cited


@pytest.mark.asyncio
async def test_mixed_no_high_confidence_falls_through_to_general(chat_env, monkeypatch):
    # MIXED with nothing above the reranker threshold -> GENERAL (no citations),
    # same retrieval-confidence fallback as KB_QUERY.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.MIXED
    chat_env.retrieve.return_value = [dict(_LOW_CONF_CHUNK)]
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.general.assert_awaited_once()
    chat_env.generate.assert_not_awaited()
    assert resp.json()["sources"] == []


@pytest.mark.asyncio
async def test_mixed_cache_key_scoped_to_mixed(chat_env, monkeypatch):
    # MIXED answers cache under their own mode-scoped key.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.MIXED
    await _post(chat_env.client)
    assert chat_env.redis.set_keys == [f"{_BASE_KEY}:MIXED"]


# --- Phase 7/8 Part A: THREAD_DOC wiring ------------------------------------
@pytest.mark.asyncio
async def test_thread_attachments_fed_to_router(chat_env, monkeypatch):
    # The router is told whether the current thread has uploaded documents.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.thread_has_docs.return_value = True
    chat_env.classify.return_value = chat_mod.KB_QUERY
    await _post(chat_env.client)
    assert chat_env.classify.await_args.kwargs["thread_has_attachments"] is True


@pytest.mark.asyncio
async def test_thread_doc_uses_thread_scoped_retrieval_with_citations(chat_env, monkeypatch):
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.thread_has_docs.return_value = True
    chat_env.classify.return_value = chat_mod.THREAD_DOC
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.thread_retrieve.assert_awaited_once()   # thread-scoped retrieval
    chat_env.retrieve.assert_not_awaited()           # NOT the shared KB search
    chat_env.general.assert_not_awaited()
    assert chat_env.generate.await_args.kwargs["mode"] == chat_mod.THREAD_DOC
    assert len(resp.json()["sources"]) == 1          # citation against uploaded doc


@pytest.mark.asyncio
async def test_thread_doc_no_high_confidence_uses_thread_aware_fallback(chat_env, monkeypatch):
    # Thread HAS an upload but no chunk clears the threshold -> the THREAD-AWARE
    # fallback (not plain GENERAL), so the answer acknowledges the document.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.thread_has_docs.return_value = True
    chat_env.classify.return_value = chat_mod.THREAD_DOC
    chat_env.thread_retrieve.return_value = [dict(_LOW_CONF_CHUNK)]
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.thread_fallback.assert_awaited_once()   # thread-aware fallback used
    chat_env.general.assert_not_awaited()            # NOT the plain GENERAL handler
    chat_env.generate.assert_not_awaited()
    body = resp.json()
    assert body["sources"] == []
    assert "uploaded document" in body["answer"]      # acknowledges the doc exists


@pytest.mark.asyncio
async def test_thread_doc_empty_retrieval_uses_thread_aware_fallback(chat_env, monkeypatch):
    # Even when thread retrieval returns nothing at all, THREAD_DOC (which only
    # fires when the thread has an upload) uses the thread-aware fallback.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.thread_has_docs.return_value = True
    chat_env.classify.return_value = chat_mod.THREAD_DOC
    chat_env.thread_retrieve.return_value = []
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.thread_fallback.assert_awaited_once()
    chat_env.general.assert_not_awaited()
    assert resp.json()["sources"] == []


@pytest.mark.asyncio
async def test_kb_fallback_still_uses_plain_general(chat_env, monkeypatch):
    # KB_QUERY / MIXED fallthrough must still use the plain GENERAL handler, not
    # the thread-aware one (there is no uploaded document to acknowledge).
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.classify.return_value = chat_mod.KB_QUERY
    chat_env.retrieve.return_value = [dict(_LOW_CONF_CHUNK)]
    resp = await _post(chat_env.client)
    assert resp.status_code == 200
    chat_env.general.assert_awaited_once()
    chat_env.thread_fallback.assert_not_awaited()
    assert resp.json()["sources"] == []


@pytest.mark.asyncio
async def test_thread_doc_retrieval_error_surfaces_as_500(chat_env, monkeypatch):
    # Same error-vs-empty distinction as KB: a thread-retrieval OUTAGE surfaces
    # as an error, never a silent uncited answer.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.thread_has_docs.return_value = True
    chat_env.classify.return_value = chat_mod.THREAD_DOC
    chat_env.thread_retrieve.side_effect = RuntimeError("mongo down")
    resp = await _post(chat_env.client)
    assert resp.status_code == 500
    chat_env.general.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_doc_cache_key_includes_thread_id(chat_env, monkeypatch):
    # THREAD_DOC keys MUST carry the thread_id ("t1" from _post) so a
    # deleted-and-recreated thread (new id) can't serve a stale cached answer.
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.thread_has_docs.return_value = True
    chat_env.classify.return_value = chat_mod.THREAD_DOC
    await _post(chat_env.client)
    assert chat_env.redis.set_keys == [f"{_BASE_KEY}:THREAD_DOC:t1"]
    # and a different thread id yields a different key (no cross-thread reuse)
    assert "t1" in chat_env.redis.set_keys[0]


@pytest.mark.asyncio
async def test_thread_doc_cache_key_differs_across_threads(chat_env, monkeypatch):
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)
    chat_env.thread_has_docs.return_value = True
    chat_env.classify.return_value = chat_mod.THREAD_DOC
    await chat_env.client.post("/chat", json={"query": _QUERY, "threadId": "old-thread"})
    await chat_env.client.post("/chat", json={"query": _QUERY, "threadId": "new-thread"})
    # Same query, two different threads -> two distinct keys, no stale reuse.
    assert chat_env.redis.set_keys == [
        f"{_BASE_KEY}:THREAD_DOC:old-thread",
        f"{_BASE_KEY}:THREAD_DOC:new-thread",
    ]
