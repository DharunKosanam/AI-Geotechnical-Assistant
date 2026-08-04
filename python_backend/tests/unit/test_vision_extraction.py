"""Vision-extraction unit tests (VISION_EXTRACTION_ENABLED).

Covers the required behaviors:
  1. a PDF with scanned pages produces vision-derived chunks carrying the
     provenance flag, page numbers and the model name;
  2. a text-only PDF triggers ZERO vision calls;
  3. a vision failure on one page leaves the other pages indexed and is
     reported in the warning;
  4. an answer grounded partly in vision-derived content says so in the
     Phase 4 scope note (naming file and pages);
  5. flag OFF: no vision call, no provenance fields, warning text exactly
     as today, fully-scanned PDFs still raise UnreadableDocumentError;
  6. the per-document vision page cap is enforced.

Deterministic: PDFs are built in-memory with PyMuPDF, the embedder is faked,
tesseract is reported unavailable, and the per-page vision call is replaced
with a stub keyed by page number (rasterization is stubbed to a marker blob so
no model or Ollama server is ever touched -- constructing ollama.Client at all
fails the test).
"""

import fitz  # PyMuPDF
import pytest

from app.core import config
from app.routers.chat import _thread_scope_note
from app.services import rag_service, vision_extraction
import app.services.file_processing as file_processing
from app.services.file_processing import UnreadableDocumentError

pytestmark = pytest.mark.unit

TID = "thread-V"
UID = "U"


# --- fixtures / fakes ---------------------------------------------------------
def _pdf(text_pages: int, image_pages: int) -> bytes:
    """Build a PDF: ``text_pages`` pages with a real text layer, then
    ``image_pages`` pages with only a drawing (no text layer -- what a scan
    looks like to the extractor)."""
    doc = fitz.open()
    for i in range(text_pages):
        page = doc.new_page()
        page.insert_text(
            (72, 72),
            f"Readable text page {i + 1}. Bearing capacity of shallow foundations. " * 5,
        )
    for _ in range(image_pages):
        page = doc.new_page()
        page.draw_rect(fitz.Rect(80, 80, 500, 700), fill=(0.6, 0.6, 0.6))
    data = doc.tobytes()
    doc.close()
    return data


class _Vec(list):
    def tolist(self):
        return list(self)


class _FakeEmbed:
    def embed(self, texts):
        return [_Vec([0.1, 0.2, 0.3, 0.4]) for _ in texts]


class _Calls:
    def __init__(self):
        self.count = 0
        self.pages = []


class _NoOllama:
    """Constructing a real client means a vision call was about to happen."""

    def __init__(self, *a, **k):
        raise AssertionError("ollama.Client must never be constructed in these tests")


def _setup(monkeypatch, enabled=True, fail_pages=frozenset(), illegible_pages=frozenset()):
    calls = _Calls()
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", enabled)
    monkeypatch.setattr(config, "VISION_MODEL", "test-vision-model")
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _FakeEmbed())
    monkeypatch.setattr(file_processing, "tesseract_available", lambda: False)
    monkeypatch.setattr(vision_extraction.ollama, "Client", _NoOllama)
    monkeypatch.setattr(
        vision_extraction, "render_page_png",
        lambda content, pn, dpi: f"PNG:{pn}".encode(),
    )

    def fake_transcribe(png, model=None):
        pn = int(png.decode().split(":")[1])
        calls.count += 1
        calls.pages.append(pn)
        if pn in fail_pages:
            raise RuntimeError(f"vision boom on page {pn}")
        if pn in illegible_pages:
            return ""
        return (
            f"Vision transcript of page {pn}: cone resistance chart, "
            f"axes labeled qc (MPa) against depth (m)."
        )

    monkeypatch.setattr(vision_extraction, "transcribe_page_image", fake_transcribe)
    return calls


def _ingest(pdf_bytes, filename="scan.pdf"):
    return rag_service._ingest_compute(
        filename, pdf_bytes, "thread_upload", UID, TID, ".pdf"
    )


# --- req 1: scanned pages -> flagged vision chunks -----------------------------
def test_scanned_pages_produce_vision_chunks_with_provenance(monkeypatch):
    calls = _setup(monkeypatch)
    docs, chunks_created, total_chars, warning = _ingest(_pdf(1, 2))

    vision_docs = [d for d in docs if d.get("visionDerived")]
    normal_docs = [d for d in docs if not d.get("visionDerived")]
    assert normal_docs, "the readable page must still produce normal chunks"
    assert vision_docs, "the scanned pages must produce vision chunks"
    assert {d["pageStart"] for d in vision_docs} == {2, 3}
    assert calls.pages == [2, 3]
    for d in vision_docs:
        assert d["metadata"]["visionDerived"] is True
        assert d["metadata"]["visionModel"] == "test-vision-model"
    # Normal chunks carry NO vision fields at all (absent, not False).
    for d in normal_docs:
        assert "visionDerived" not in d
        assert "visionDerived" not in d["metadata"]
        assert "visionModel" not in d["metadata"]
    assert chunks_created == len(docs)
    # Chip warning reflects the successful transcription, flagged as
    # model-interpreted, and no longer claims the pages were not indexed.
    assert "AI vision transcribed 2 of them (pages 2, 3)" in warning
    assert "model-interpreted, not verbatim" in warning
    assert "and were not indexed." not in warning


def test_fully_scanned_pdf_rescued_by_vision(monkeypatch):
    _setup(monkeypatch)
    docs, _, _, warning = _ingest(_pdf(0, 2))
    assert docs and all(d.get("visionDerived") for d in docs)
    assert {d["pageStart"] for d in docs} == {1, 2}
    assert "AI vision transcribed 2 of them (pages 1, 2)" in warning


def test_fully_scanned_pdf_all_vision_failed_raises_original_error(monkeypatch):
    _setup(monkeypatch, fail_pages={1, 2})
    with pytest.raises(UnreadableDocumentError):
        _ingest(_pdf(0, 2))


# --- req 2: text-only PDF -> zero vision calls ---------------------------------
def test_text_only_pdf_makes_zero_vision_calls(monkeypatch):
    calls = _setup(monkeypatch)
    docs, _, _, warning = _ingest(_pdf(2, 0), filename="text.pdf")
    assert calls.count == 0
    assert warning is None
    assert not any(d.get("visionDerived") for d in docs)


# --- req 3: one failed page never fails the ingest -----------------------------
def test_vision_failure_on_one_page_is_isolated_and_reported(monkeypatch):
    _setup(monkeypatch, fail_pages={2})
    docs, _, _, warning = _ingest(_pdf(1, 2))
    assert {d["pageStart"] for d in docs if d.get("visionDerived")} == {3}
    assert any(not d.get("visionDerived") for d in docs)  # page 1 still indexed
    assert "AI vision transcribed 1 of them (page 3)" in warning
    assert "1 could not be read by vision and was not indexed (page 2)" in warning


def test_illegible_page_reported_as_failed_not_indexed(monkeypatch):
    # The model answering NO_LEGIBLE_CONTENT (stubbed as "") is a per-page
    # failure, not a chunk of empty text.
    _setup(monkeypatch, illegible_pages={2, 3})
    docs, _, _, warning = _ingest(_pdf(1, 2))
    assert not any(d.get("visionDerived") for d in docs)
    assert "2 could not be read by vision and were not indexed (pages 2, 3)" in warning


# --- req 6: page cap ------------------------------------------------------------
def test_vision_page_cap_enforced(monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setattr(config, "VISION_MAX_PAGES_PER_DOC", 2)
    docs, _, _, warning = _ingest(_pdf(1, 4))  # scanned pages 2..5
    assert calls.count == 2
    assert calls.pages == [2, 3]  # lowest page numbers first, deterministic
    assert {d["pageStart"] for d in docs if d.get("visionDerived")} == {2, 3}
    assert "2 exceeded the vision page cap and were not indexed (pages 4, 5)" in warning


# --- req 5: flag off -> byte-identical to today ---------------------------------
def test_flag_off_no_vision_call_and_warning_unchanged(monkeypatch):
    calls = _setup(monkeypatch, enabled=False)
    docs, _, _, warning = _ingest(_pdf(1, 2))
    assert calls.count == 0
    assert warning == (
        "2 of 3 pages had no readable text (scanned or image-only) "
        "and were not indexed."
    )
    for d in docs:
        assert "visionDerived" not in d
        assert "visionDerived" not in d["metadata"]
        assert "visionModel" not in d["metadata"]


def test_flag_off_fully_scanned_pdf_still_raises(monkeypatch):
    calls = _setup(monkeypatch, enabled=False)
    with pytest.raises(UnreadableDocumentError):
        _ingest(_pdf(0, 2))
    assert calls.count == 0


# --- req 4: the scope note names vision-derived content -------------------------
def _scope(**over):
    scope = {
        "searched": ["scan.pdf"],
        "grounded": ["scan.pdf"],
        "no_relevant": [],
        "excluded": [],
        "pending": [],
        "failed": [],
    }
    scope.update(over)
    return scope


def test_vision_scope_collects_pages_and_ignores_low_confidence():
    chunks = [
        {"filename": "a.pdf", "metadata": {"visionDerived": True}, "pageStart": 3},
        {"filename": "a.pdf", "metadata": {"visionDerived": True}, "pageStart": 2},
        {"filename": "a.pdf", "metadata": {}, "pageStart": 1},
        {"filename": "b.pdf", "metadata": {"visionDerived": True}, "pageStart": 9,
         "low_confidence": True},
    ]
    assert rag_service._vision_scope(chunks) == [
        {"filename": "a.pdf", "pages": [2, 3], "image": False}
    ]


def test_note_names_vision_pages_even_for_single_document():
    # Pre-vision, a single-document thread produced NO note; vision content
    # must break that silence -- the honesty requirement outranks brevity.
    note = _thread_scope_note(_scope(vision=[{"filename": "scan.pdf", "pages": [2, 3]}]))
    assert "AI vision transcription" in note
    assert "scan.pdf pages 2, 3" in note
    assert "not verbatim document text" in note


def test_note_without_vision_unchanged_single_doc_silent():
    assert _thread_scope_note(_scope()) == ""
    assert _thread_scope_note(_scope(vision=[])) == ""


def test_note_multi_doc_keeps_phase4_wording_and_appends_vision():
    note = _thread_scope_note(_scope(
        searched=["a.pdf", "scan.pdf"],
        grounded=["a.pdf", "scan.pdf"],
        vision=[{"filename": "scan.pdf", "pages": [4]}],
    ))
    assert note.startswith("_Searched 2 attached documents: a.pdf and scan.pdf.")
    assert "This answer is grounded in a.pdf and scan.pdf." in note
    assert "scan.pdf page 4" in note
    assert "model-generated interpretation, not verbatim document text" in note


def test_note_sampled_single_doc_fully_read_gains_vision_sentence():
    # Fully-read single doc used to return "" -- with vision content in the
    # sample, the note must still name it.
    note = _thread_scope_note(_scope(
        sampled=[{"filename": "scan.pdf", "sampled": 3, "total": 3}],
        vision=[{"filename": "scan.pdf", "pages": [2]}],
    ))
    assert "AI vision transcription" in note and "scan.pdf page 2" in note


# --- the sampler fills scope['vision'] (fake collection) -------------------------
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


def _chunk(filename, i, page, vision=False):
    meta = {"visionDerived": True, "visionModel": "test-vision-model"} if vision else {}
    return {
        "_id": f"c-{filename}-{i}",
        "category": "thread_upload",
        "threadId": TID,
        "userId": UID,
        "filename": filename,
        "chunkIndex": i,
        "pageStart": page,
        "text": f"{filename} chunk {i}",
        "metadata": meta,
    }


async def test_sampler_fills_vision_scope(monkeypatch):
    docs = [
        _chunk("scan.pdf", 0, 1),
        _chunk("scan.pdf", 1, 2, vision=True),
        _chunk("scan.pdf", 2, 3, vision=True),
    ]
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    scope = {}
    out = await rag_service.sample_thread_documents(TID, UID, budget=8, scope_out=scope)
    assert len(out) == 3
    assert scope["vision"] == [{"filename": "scan.pdf", "pages": [2, 3], "image": False}]


async def test_sampler_without_vision_chunks_reports_empty_vision_scope(monkeypatch):
    docs = [_chunk("plain.pdf", i, i + 1) for i in range(3)]
    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    scope = {}
    await rag_service.sample_thread_documents(TID, UID, budget=8, scope_out=scope)
    assert scope["vision"] == []
