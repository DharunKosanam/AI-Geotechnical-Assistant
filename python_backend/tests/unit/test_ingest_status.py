"""Phase 1 unit tests: ingestion lifecycle (pending/ready/failed), the
staleness rule, the pending/failed scope-note rendering, the Phase 2 piggyback
assumption (parent with zero chunks still reports has-attachments), and the
fingerprint interaction (mid-ingestion vs completed must not share a key).
"""

from datetime import datetime, timedelta

import pytest

from app.core import config
from app.routers.chat import _mode_cache_key, _thread_scope_note
from app.services import rag_service
from app.services.rag_service import effective_ingest_status

pytestmark = pytest.mark.unit

TID = "thread-I"
UID = "U"
NOW = datetime(2026, 7, 30, 12, 0, 0)


# --- fakes (as in test_thread_doc_cache_key) ---------------------------------
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


def _parent(filename, status, chunk_count=0, created=None, error=None):
    d = {
        "_id": f"p-{filename}",
        "category": "thread_upload",
        "threadId": TID,
        "userId": UID,
        "filename": filename,
        "chunkCount": chunk_count,
        "createdAt": created or NOW,
    }
    if status is not None:
        d["status"] = status
    if error is not None:
        d["error"] = error
    return d


async def _inventory(monkeypatch, parents):
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(parents))
    return await rag_service.thread_document_inventory(TID, UID)


# --- effective_ingest_status: the lifecycle mapping --------------------------
def test_processed_maps_to_ready():
    assert effective_ingest_status(_parent("a.pdf", "processed", 40), now=NOW) == ("ready", None)


def test_processing_maps_to_pending_within_threshold():
    doc = _parent("a.pdf", "processing", created=NOW - timedelta(seconds=30))
    assert effective_ingest_status(doc, now=NOW) == ("pending", None)


def test_stale_processing_reports_failed_with_timeout_reason(monkeypatch):
    monkeypatch.setattr(config, "INGEST_PENDING_TIMEOUT_SECONDS", 600)
    doc = _parent("a.pdf", "processing", created=NOW - timedelta(seconds=601))
    status, reason = effective_ingest_status(doc, now=NOW)
    assert status == "failed"
    assert "timed out" in reason and "Re-upload" in reason


def test_failed_carries_stored_reason():
    doc = _parent("a.pdf", "failed", error="a.pdf looks like a scanned PDF ...")
    assert effective_ingest_status(doc, now=NOW) == ("failed", "a.pdf looks like a scanned PDF ...")


def test_failed_without_reason_gets_generic():
    assert effective_ingest_status(_parent("a.pdf", "failed"), now=NOW) == ("failed", "Ingestion failed")


def test_legacy_doc_without_status_is_ready():
    assert effective_ingest_status(_parent("a.pdf", None, 12), now=NOW) == ("ready", None)


# --- req 6: the Phase 2 piggyback assumption, tested directly ----------------
# NOTE: thread_document_inventory derives status with the REAL clock (it
# calls effective_ingest_status(now=datetime.now()) internally), so any
# "processing" parent fed to _inventory must carry a FRESH createdAt. The
# fixed NOW constant is for the direct effective_ingest_status tests only --
# using it here made these tests age into failures once wall-clock passed
# NOW + INGEST_PENDING_TIMEOUT_SECONDS (the staleness rule kicked in).
async def test_parent_with_zero_chunks_reports_has_attachments(monkeypatch):
    # Upload registered, ingestion still running: parent exists, ZERO chunks.
    has, fp, states = await _inventory(
        monkeypatch,
        [_parent("fresh.pdf", "processing", chunk_count=0, created=datetime.now())],
    )
    assert has is True          # the router CAN classify THREAD_DOC
    assert fp != ""
    # sourceType joined the state dict for diagram labeling (Phase 4); None
    # for every ordinary document.
    assert states == [{
        "filename": "fresh.pdf", "status": "pending", "reason": None,
        "sourceType": None,
        "fileType": None,   # metadata.fileType surfaced for the router (additive)
    }]


# --- fingerprint x lifecycle: mid-ingestion vs completed ---------------------
async def test_pending_and_completed_do_not_share_cache_key(monkeypatch):
    base = "u:q"
    _, fp_mid, _ = await _inventory(
        monkeypatch, [_parent("a.pdf", "processed", 40),
                      _parent("big.pdf", "processing", chunk_count=0,
                              created=datetime.now())]
    )
    _, fp_done, _ = await _inventory(
        monkeypatch, [_parent("a.pdf", "processed", 40),
                      _parent("big.pdf", "processed", 1600)]
    )
    assert fp_mid != fp_done
    assert (_mode_cache_key(base, "THREAD_DOC", TID, fp_mid)
            != _mode_cache_key(base, "THREAD_DOC", TID, fp_done))


async def test_pending_to_failed_changes_key_even_at_zero_chunks(monkeypatch):
    # chunkCount stays 0 in both states; the status leg of the triple must
    # still separate them, or a cached "still being processed" answer would
    # survive the failure.
    _, fp_pending, _ = await _inventory(
        monkeypatch, [_parent("big.pdf", "processing", chunk_count=0,
                              created=datetime.now())]
    )
    _, fp_failed, _ = await _inventory(
        monkeypatch, [_parent("big.pdf", "failed", chunk_count=0, error="boom")]
    )
    assert fp_pending != fp_failed


# --- scope note: pending / failed rendering ----------------------------------
def _ready_scope(**over):
    scope = {
        "searched": ["ready.pdf"],
        "grounded": ["ready.pdf"],
        "no_relevant": [],
        "excluded": [],
        "pending": [],
        "failed": [],
    }
    scope.update(over)
    return scope


def test_pending_document_rendered_distinctly():
    note = _thread_scope_note(_ready_scope(pending=["uploading.pdf"]))
    assert "Searched 1 of 2 attached documents: ready.pdf." in note
    assert "uploading.pdf is still being processed and was not searched." in note
    assert "No content relevant" not in note   # NOT searched-and-empty
    assert "matched this question" not in note  # NOT excluded


def test_failed_document_named_with_reason_and_does_not_block_ready():
    note = _thread_scope_note(_ready_scope(
        failed=[{"filename": "broken.pdf", "reason": "scanned PDF, no text layer"}]
    ))
    # The ready document still grounds the answer...
    assert "This answer is grounded in ready.pdf." in note
    # ...and the failed one is named with its stored reason, as unsearched.
    assert '"broken.pdf" could not be processed and was not searched (scanned PDF, no text layer)' in note


def test_mixed_ready_pending_failed_all_three_states_in_one_note():
    note = _thread_scope_note(_ready_scope(
        pending=["cooking.pdf"],
        failed=[{"filename": "broken.pdf", "reason": "Ingestion timed out"}],
    ))
    assert "Searched 1 of 3 attached documents: ready.pdf." in note
    assert "cooking.pdf is still being processed" in note
    assert '"broken.pdf" could not be processed' in note and "Ingestion timed out" in note


def test_single_ready_document_no_note():
    # Identical to today: one attachment, fully ready -> no scope statement.
    assert _thread_scope_note(_ready_scope()) == ""


def test_all_ready_multi_doc_wording_unchanged_from_phase4():
    note = _thread_scope_note({
        "searched": ["a.pdf", "b.pdf"], "grounded": ["a.pdf"],
        "no_relevant": ["b.pdf"], "excluded": [], "pending": [], "failed": [],
    })
    # The Phase 4 header must not gain the "N of M" form when all are searched.
    assert note.startswith("_Searched 2 attached documents: a.pdf and b.pdf.")


def test_single_pending_plus_single_ready_note_appears():
    # Two attachments, one searchable: the note must appear even though only
    # ONE document was searched (the pre-Phase-1 gate keyed on searched >= 2).
    note = _thread_scope_note(_ready_scope(pending=["uploading.pdf"]))
    assert note != ""


# --- Issue B: the structured sampler (unit) ----------------------------------
def _text_chunk(filename, i):
    return {
        "_id": f"c-{filename}-{i}",
        "category": "thread_upload",
        "threadId": TID,
        "userId": UID,
        "filename": filename,
        "chunkIndex": i,
        "text": f"{filename} chunk {i}",
        "metadata": {},
    }


async def test_sampler_single_doc_opening_plus_spread(monkeypatch):
    docs = [_text_chunk("one.pdf", i) for i in range(30)]
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    scope = {}
    out = await rag_service.sample_thread_documents(TID, UID, budget=8, scope_out=scope)
    idxs = [int(c["id"].rsplit("-", 1)[1]) for c in out]
    assert len(out) == 8
    assert idxs[0] == 0                      # opening chunk always included
    assert idxs == sorted(idxs)              # reading order
    assert idxs[-1] >= 25                    # spread reaches the tail
    assert all(c["low_confidence"] is False for c in out)
    assert scope["sampled"] == [{"filename": "one.pdf", "sampled": 8, "total": 30}]


async def test_sampler_two_docs_budget_split(monkeypatch):
    docs = ([_text_chunk("a.pdf", i) for i in range(40)]
            + [_text_chunk("b.pdf", i) for i in range(56)])
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    scope = {}
    out = await rag_service.sample_thread_documents(TID, UID, budget=8, scope_out=scope)
    by_doc = {}
    for c in out:
        by_doc.setdefault(c["filename"], []).append(c)
    assert set(by_doc) == {"a.pdf", "b.pdf"}          # both docs sampled
    assert len(by_doc["a.pdf"]) == 4 and len(by_doc["b.pdf"]) == 4
    assert scope["sampled"] == [
        {"filename": "a.pdf", "sampled": 4, "total": 40},
        {"filename": "b.pdf", "sampled": 4, "total": 56},
    ]


async def test_sampler_small_doc_fully_read(monkeypatch):
    docs = [_text_chunk("tiny.pdf", i) for i in range(3)]
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    scope = {}
    out = await rag_service.sample_thread_documents(TID, UID, budget=8, scope_out=scope)
    assert len(out) == 3
    assert scope["sampled"] == [{"filename": "tiny.pdf", "sampled": 3, "total": 3}]
    # Fully-read single doc: the note stays silent (coverage IS complete).
    scope.update({"pending": [], "failed": []})
    assert _thread_scope_note(scope) == ""


def test_sampled_note_partial_single_doc():
    scope = {
        "searched": ["long.pdf"], "grounded": ["long.pdf"],
        "no_relevant": [], "excluded": [],
        "sampled": [{"filename": "long.pdf", "sampled": 8, "total": 370}],
        "pending": [], "failed": [],
    }
    note = _thread_scope_note(scope)
    assert "draws on a sample: 8 of 370 sections from long.pdf" in note
    assert "Details outside the sample may not be reflected." in note
    assert "Searched" not in note            # sampled, not searched


def test_sampled_note_keeps_pending_and_failed_lines():
    scope = {
        "searched": ["long.pdf"], "grounded": ["long.pdf"],
        "no_relevant": [], "excluded": [],
        "sampled": [{"filename": "long.pdf", "sampled": 8, "total": 370}],
        "pending": ["cooking.pdf"],
        "failed": [{"filename": "broken.pdf", "reason": "no text layer"}],
    }
    note = _thread_scope_note(scope)
    assert "cooking.pdf is still being processed" in note
    assert '"broken.pdf" could not be processed' in note
