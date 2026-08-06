"""Feature A integration tests: the /chat/formats routes.

Drives the real ASGI app with auth overridden and the heavy pieces (inventory,
chunk loading, the engine) monkeypatched, asserting the route contracts:

  * flag OFF -> status says disabled, stream 404s, and the engine/inventory
    are provably never called (no new code path reachable).
  * flag ON  -> SSE stream carries start/progress/token/done, the done event
    records the engine path, the answer ends with the coverage note, and the
    generated document is persisted to the thread.
  * Phase 1: pending/failed documents are stated, never silently omitted; a
    thread with nothing ready gets the deterministic status answer.
  * Concurrency guard: a second request is refused with 409, never stacked.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from app.core import config
from app.core.rate_limit import limiter, rate_limit_identify
from app.dependencies.auth import get_current_user
from app.main import app
from models import User
import app.routers.chat as chat_mod

pytestmark = pytest.mark.integration


class _FakeMessages:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="msg_id")


def _chunk(filename, text, vision=False, page=None):
    meta = {"fileType": ".pdf"}
    if vision:
        meta["visionDerived"] = True
    return {
        "id": "c1",
        "filename": filename,
        "text": text,
        "category": "thread_upload",
        "metadata": meta,
        "pageStart": page,
        "low_confidence": False,
    }


async def _fake_engine_factory(spy):
    async def fake_engine(format_key, doc_blocks, context_header="", emit=None, progress=None):
        spy["calls"].append({
            "format": format_key,
            "doc_blocks": doc_blocks,
            "context_header": context_header,
        })
        if progress is not None:
            await progress("map", 1, 2)
            await progress("map", 2, 2)
            await progress("reduce", 1, 1)
        if emit is not None:
            await emit("GENERATED DOCUMENT ")
            await emit("[Source: uploaded_report]")
        return "GENERATED DOCUMENT [Source: uploaded_report]", {
            "engine": "map_reduce", "chunks": 2, "estPromptTokens": 100,
            "mapCalls": 2, "reduceLevels": 1,
        }
    return fake_engine


@pytest_asyncio.fixture
async def formats_env(monkeypatch):
    fake_user = User(id="user-1", email="u@example.com", hashed_password="x")
    app.dependency_overrides[rate_limit_identify] = lambda: fake_user
    app.dependency_overrides[get_current_user] = lambda: fake_user
    monkeypatch.setattr(limiter, "enabled", False)

    fake_messages = _FakeMessages()
    monkeypatch.setattr(chat_mod, "messages_collection", fake_messages)

    inventory = AsyncMock(return_value=(
        True, "fp1", [{"filename": "report.pdf", "status": "ready", "reason": None}]
    ))
    monkeypatch.setattr(chat_mod, "thread_document_inventory", inventory)

    loader = AsyncMock(return_value={
        "report.pdf": [
            _chunk("report.pdf", "First chunk."),
            _chunk("report.pdf", "Second chunk."),
        ]
    })
    monkeypatch.setattr(chat_mod, "load_full_thread_documents", loader)

    engine_spy = {"calls": []}
    monkeypatch.setattr(
        chat_mod, "generate_format_document", await _fake_engine_factory(engine_spy)
    )

    # A clean guard for every test.
    monkeypatch.setattr(chat_mod, "_format_job_active", False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(
            client=client,
            inventory=inventory,
            loader=loader,
            engine=engine_spy,
            messages=fake_messages,
        )

    app.dependency_overrides.pop(rate_limit_identify, None)
    app.dependency_overrides.pop(get_current_user, None)


def _events(sse_text):
    """Parse an SSE body into [(event, payload_dict)]."""
    out = []
    for record in sse_text.split("\n\n"):
        name, data = None, None
        for line in record.split("\n"):
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            out.append((name, data))
    return out


async def _stream(client, fmt="briefing", thread="t1"):
    return await client.post(
        "/chat/formats/stream", json={"threadId": thread, "format": fmt}
    )


# --- flag OFF: nothing new is reachable -------------------------------------
@pytest.mark.asyncio
async def test_flag_off_status_disabled_and_inventory_untouched(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", False)
    resp = await formats_env.client.get("/chat/formats/status?threadId=t1")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}
    formats_env.inventory.assert_not_called()


@pytest.mark.asyncio
async def test_flag_off_stream_404s_and_engine_never_runs(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", False)
    resp = await _stream(formats_env.client)
    assert resp.status_code == 404
    assert formats_env.engine["calls"] == []
    assert formats_env.messages.inserted == []


# --- flag ON: status handshake ----------------------------------------------
@pytest.mark.asyncio
async def test_status_lists_formats_and_ready_count(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    resp = await formats_env.client.get("/chat/formats/status?threadId=t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["readyDocuments"] == 1
    assert body["generating"] is False
    assert {f["key"] for f in body["formats"]} == {
        "study_guide", "briefing", "faq", "timeline", "glossary"
    }


# --- flag ON: the stream contract -------------------------------------------
@pytest.mark.asyncio
async def test_stream_generates_with_coverage_note_and_persists(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    resp = await _stream(formats_env.client)
    assert resp.status_code == 200
    events = _events(resp.text)
    names = [n for n, _ in events]
    assert names[0] == "start"
    assert "progress" in names and "token" in names
    assert names[-1] == "done"

    done = events[-1][1]
    assert done["engine"] == "map_reduce"
    assert done["format"] == "briefing"
    assert done["coverage"] == [
        {"filename": "report.pdf", "sampled": 2, "total": 2, "status": "ready"}
    ]
    assert done["sources"][0]["filename"] == "report.pdf"
    assert done["sources"][0]["url"] is None  # private upload: no external link

    # The streamed text ends with the always-stated coverage note.
    streamed = "".join(d["text"] for n, d in events if n == "token")
    assert "GENERATED DOCUMENT" in streamed
    assert "This generated document draws on all sections of report.pdf." in streamed

    # Persisted to the thread with the same text and the format/engine marks.
    assert len(formats_env.messages.inserted) == 1
    saved = formats_env.messages.inserted[0]
    assert saved["role"] == "assistant"
    assert saved["content"] == streamed
    assert saved["format"] == "briefing"
    assert saved["engine"] == "map_reduce"
    assert saved["threadId"] == "t1" and saved["userId"] == "user-1"


@pytest.mark.asyncio
async def test_unknown_format_400s(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    resp = await _stream(formats_env.client, fmt="haiku")
    assert resp.status_code == 400
    assert formats_env.engine["calls"] == []


# --- multi-source attribution ------------------------------------------------
@pytest.mark.asyncio
async def test_multi_source_thread_attributes_per_source(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    formats_env.inventory.return_value = (
        True, "fp2",
        [{"filename": "a.pdf", "status": "ready", "reason": None},
         {"filename": "b.pdf", "status": "ready", "reason": None}],
    )
    formats_env.loader.return_value = {
        "a.pdf": [_chunk("a.pdf", "Alpha content.")],
        "b.pdf": [_chunk("b.pdf", "Beta content.", vision=True, page=3)],
    }
    resp = await _stream(formats_env.client, fmt="study_guide")
    events = _events(resp.text)
    done = events[-1][1]

    # The engine received per-document blocks and the multi-doc header.
    call = formats_env.engine["calls"][0]
    assert [fn for fn, _ in call["doc_blocks"]] == ["a.pdf", "b.pdf"]
    assert "[ATTACHED DOCUMENTS: a.pdf, b.pdf]" in call["context_header"]

    # Coverage names both, sources carry vision provenance for b only.
    assert {c["filename"] for c in done["coverage"]} == {"a.pdf", "b.pdf"}
    by_file = {s["filename"]: s for s in done["sources"]}
    assert "visionDerived" not in by_file["a.pdf"]
    assert by_file["b.pdf"]["visionDerived"] is True
    assert by_file["b.pdf"]["visionPages"] == [3]

    streamed = "".join(d["text"] for n, d in events if n == "token")
    assert "draws on all sections of a.pdf and b.pdf" in streamed
    # Vision provenance sentence from the shared scope-note machinery.
    assert "AI vision" in streamed


# --- Phase 1: pending / failed ----------------------------------------------
@pytest.mark.asyncio
async def test_pending_document_is_stated_in_note_and_coverage(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    formats_env.inventory.return_value = (
        True, "fp3",
        [{"filename": "report.pdf", "status": "ready", "reason": None},
         {"filename": "later.pdf", "status": "pending", "reason": None}],
    )
    resp = await _stream(formats_env.client)
    events = _events(resp.text)
    streamed = "".join(d["text"] for n, d in events if n == "token")
    assert "later.pdf" in streamed
    assert "still being processed" in streamed
    done = events[-1][1]
    assert {"filename": "later.pdf", "status": "pending"} in done["coverage"]


@pytest.mark.asyncio
async def test_nothing_ready_returns_deterministic_status_answer(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    formats_env.inventory.return_value = (
        True, "fp4",
        [{"filename": "slow.pdf", "status": "pending", "reason": None},
         {"filename": "broken.pdf", "status": "failed", "reason": "Unreadable file"}],
    )
    resp = await _stream(formats_env.client)
    events = _events(resp.text)
    streamed = "".join(d["text"] for n, d in events if n == "token")
    assert "slow.pdf" in streamed and "still being processed" in streamed
    assert "broken.pdf" in streamed and "Unreadable file" in streamed
    # No engine run, nothing persisted, done carries the none-engine marker.
    assert formats_env.engine["calls"] == []
    assert events[-1][1]["engine"] == "none"
    assert formats_env.messages.inserted == []


# --- concurrency guard -------------------------------------------------------
@pytest.mark.asyncio
async def test_second_concurrent_request_is_refused_not_stacked(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    # Simulate an in-flight job exactly as the endpoint records one.
    monkeypatch.setattr(chat_mod, "_format_job_active", True)
    resp = await _stream(formats_env.client)
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]
    assert formats_env.engine["calls"] == []
    # Status reflects the busy state for the frontend's disabled tooltip.
    status_resp = await formats_env.client.get("/chat/formats/status")
    assert status_resp.json()["generating"] is True


@pytest.mark.asyncio
async def test_guard_clears_after_a_completed_run(formats_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    resp = await _stream(formats_env.client)
    assert resp.status_code == 200
    assert chat_mod._format_job_active is False
    # And a follow-up request is accepted.
    resp2 = await _stream(formats_env.client, fmt="faq")
    assert resp2.status_code == 200


# --- client disconnect: guard cleared, nothing persisted ---------------------
@pytest.mark.asyncio
async def test_disconnect_at_start_event_clears_guard(formats_env, monkeypatch):
    """Regression: the first yield used to sit outside the try/finally, so a
    disconnect at that exact suspension point leaked the single-job guard
    set forever (every later request 409d until restart)."""
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)
    fake_user = User(id="user-1", email="u@example.com", hashed_password="x")
    payload = chat_mod.FormatGenerateRequest(threadId="t1", format="briefing")
    chat_mod._format_job_active = True  # as the endpoint sets before streaming
    gen = chat_mod._sse_format_turn(payload, fake_user)
    first = await gen.__anext__()
    assert "start" in first
    await gen.aclose()  # client gone while suspended at the start event
    assert chat_mod._format_job_active is False
    assert formats_env.messages.inserted == []


@pytest.mark.asyncio
async def test_disconnect_mid_generation_cancels_and_persists_nothing(
    formats_env, monkeypatch
):
    monkeypatch.setattr(config, "SOURCE_FORMATS_ENABLED", True)

    started = asyncio.Event()

    async def slow_engine(format_key, doc_blocks, context_header="", emit=None, progress=None):
        started.set()
        await asyncio.sleep(30)  # cancelled long before this completes
        return "never", {"engine": "single_call", "chunks": 1, "estPromptTokens": 1}

    monkeypatch.setattr(chat_mod, "generate_format_document", slow_engine)
    fake_user = User(id="user-1", email="u@example.com", hashed_password="x")
    payload = chat_mod.FormatGenerateRequest(threadId="t1", format="briefing")
    chat_mod._format_job_active = True
    gen = chat_mod._sse_format_turn(payload, fake_user)
    await gen.__anext__()  # start event (turn task not yet created)
    await gen.__anext__()  # first progress event; the turn is now running
    await asyncio.wait_for(started.wait(), 5)  # engine underway
    await gen.aclose()  # disconnect mid-generation
    assert chat_mod._format_job_active is False
    # No partial document was persisted: the insert runs only after the
    # engine returns, and the cancel arrived before that.
    assert formats_env.messages.inserted == []


# --- scope-note builder: format-specific parameters --------------------------
def test_scope_note_default_wording_unchanged():
    scope = {
        "searched": ["a.pdf", "b.pdf"],
        "grounded": ["a.pdf", "b.pdf"],
        "no_relevant": [], "excluded": [],
        "sampled": [
            {"filename": "a.pdf", "sampled": 4, "total": 100},
            {"filename": "b.pdf", "sampled": 4, "total": 8},
        ],
    }
    note = chat_mod._thread_scope_note(scope)
    assert note.startswith("_This document-level answer draws on a sample:")
    assert "4 of 100 sections from a.pdf" in note


def test_scope_note_single_doc_full_coverage_silent_by_default():
    scope = {
        "searched": ["a.pdf"], "grounded": ["a.pdf"],
        "no_relevant": [], "excluded": [],
        "sampled": [{"filename": "a.pdf", "sampled": 5, "total": 5}],
    }
    assert chat_mod._thread_scope_note(scope) == ""


def test_scope_note_formats_variant_always_states_full_coverage():
    scope = {
        "searched": ["a.pdf"], "grounded": ["a.pdf"],
        "no_relevant": [], "excluded": [],
        "sampled": [{"filename": "a.pdf", "sampled": 5, "total": 5}],
    }
    note = chat_mod._thread_scope_note(
        scope, subject_phrase="This generated document", always_state_coverage=True
    )
    assert note == "_This generated document draws on all sections of a.pdf._"
