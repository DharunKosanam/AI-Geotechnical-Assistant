"""Direct image upload via vision (VISION_EXTRACTION_ENABLED).

Covers the required behaviors:
  1. JPEG, PNG and WebP each ingest to exactly ONE visionDerived chunk with
     the model name and NO pageStart (omitted, not a fake page 1);
  2. an oversized image is downscaled before the vision call, never rejected;
  3. an illegible image (model refusal) or a failed call fails the upload
     cleanly with a user-facing reason and indexes ZERO chunks;
  4. an answer drawing on an image says so in the scope note -- as an AI
     description of an uploaded image, not document text;
  5. a non-image, non-document type stays rejected (flag on or off);
  6. flag OFF: .webp rejected and PNG/JPG handled exactly as today (OCR path
     or up-front rejection), with zero vision involvement;
  7. a mixed thread (one PDF + one image) respects the Phase 3 per-document
     quota -- the image's single chunk gets a context slot.

Deterministic: images built in-memory with Pillow, the embedder faked,
tesseract forced unavailable where relevant, and describe_uploaded_image
(the image-prompt call) stubbed -- transcribe_page_image (the PDF-page
prompt) is booby-trapped so the image path can never silently reuse it.
Constructing ollama.Client at all fails the test.
"""

import io
import math

import pytest
from fastapi import HTTPException
from PIL import Image

from app.core import config
from app.routers import files as files_router
from app.routers.chat import _thread_scope_note
from app.services import rag_service, vision_extraction
import app.services.file_processing as file_processing
from app.services.file_processing import UnreadableDocumentError

pytestmark = pytest.mark.unit

TID = "thread-IMG"
UID = "U"


# --- fixtures / fakes ---------------------------------------------------------
def _image_bytes(fmt: str, size=(320, 200), color=(180, 40, 40)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class _Vec(list):
    def tolist(self):
        return list(self)


class _FakeEmbed:
    def embed(self, texts):
        return [_Vec([0.1, 0.2, 0.3, 0.4]) for _ in texts]


class _NoOllama:
    def __init__(self, *a, **k):
        raise AssertionError("ollama.Client must never be constructed in these tests")


TRANSCRIPT = (
    "Photo of a CPT rig set up on a gravel pad; the data screen shows "
    "qc around 14 MPa at 8 m depth."
)


def _setup(monkeypatch, enabled=True, transcript=TRANSCRIPT, fail=False):
    """Common stubbing. Returns a dict capturing vision-call activity."""
    seen = {"count": 0, "png": None}
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", enabled)
    monkeypatch.setattr(config, "VISION_MODEL", "test-vision-model")
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _FakeEmbed())
    monkeypatch.setattr(vision_extraction.ollama, "Client", _NoOllama)

    def fake_describe(png, model=None):
        seen["count"] += 1
        seen["png"] = png
        if fail:
            raise RuntimeError("vision transport boom")
        return transcript

    def page_prompt_trap(png, model=None):
        raise AssertionError(
            "transcribe_page_image (the scanned-PAGE prompt) must never be "
            "used for a direct image upload"
        )

    monkeypatch.setattr(vision_extraction, "describe_uploaded_image", fake_describe)
    monkeypatch.setattr(vision_extraction, "transcribe_page_image", page_prompt_trap)
    return seen


def _ingest(content, filename):
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    return rag_service._ingest_compute(
        filename, content, "thread_upload", UID, TID, ext
    )


# --- req 1: JPEG / PNG / WebP -> exactly one flagged chunk ----------------------
@pytest.mark.parametrize("fmt,ext", [("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp")])
def test_image_formats_ingest_to_one_vision_chunk(monkeypatch, fmt, ext):
    seen = _setup(monkeypatch)
    docs, chunks_created, total_chars, warning = _ingest(
        _image_bytes(fmt), f"site-photo.{ext}"
    )
    assert seen["count"] == 1          # ONE vision call, no rasterization loop
    assert chunks_created == 1 and len(docs) == 1
    doc = docs[0]
    assert doc["visionDerived"] is True
    assert doc["metadata"]["visionDerived"] is True
    assert doc["metadata"]["visionModel"] == "test-vision-model"
    assert doc["text"] == TRANSCRIPT
    # A standalone image has no page: the field is OMITTED, not a fake 1.
    assert "pageStart" not in doc
    assert "pageStart" not in doc["metadata"]
    assert doc["metadata"]["fileType"] == f".{ext}"
    # The chip states the image was interpreted by vision.
    assert "was read by AI vision (test-vision-model)" in warning
    assert "model-generated description" in warning
    assert "not" in warning and "extracted text" in warning


# --- req 2: oversized image downscaled, never rejected --------------------------
def test_oversized_image_downscaled_not_rejected(monkeypatch):
    seen = _setup(monkeypatch)
    monkeypatch.setattr(config, "VISION_IMAGE_MAX_DIM", 64)
    docs, *_ = _ingest(_image_bytes("PNG", size=(300, 100)), "huge.png")
    assert len(docs) == 1              # accepted, not rejected
    sent = Image.open(io.BytesIO(seen["png"]))
    assert max(sent.size) == 64        # longest edge capped
    assert sent.size == (64, 21)       # aspect ratio preserved (300:100)


def test_small_image_not_upscaled(monkeypatch):
    seen = _setup(monkeypatch)
    _ingest(_image_bytes("PNG", size=(320, 200)), "small.png")
    sent = Image.open(io.BytesIO(seen["png"]))
    assert sent.size == (320, 200)


# --- req 3: illegible or failed -> clean failure, zero chunks -------------------
def test_blank_image_fails_cleanly_with_zero_chunks(monkeypatch):
    # "" is what describe_uploaded_image returns on the NO_LEGIBLE_CONTENT
    # sentinel, which now means: nothing at all could be DESCRIBED (blank,
    # corrupt, or fully obscured) -- not merely "no text".
    _setup(monkeypatch, transcript="")
    with pytest.raises(UnreadableDocumentError) as exc:
        _ingest(_image_bytes("PNG"), "blurry.png")
    assert "AI vision could not make out any content in blurry.png" in str(exc.value)
    assert "blank, corrupt, or too obscured" in str(exc.value)


def test_vision_call_failure_fails_cleanly(monkeypatch):
    _setup(monkeypatch, fail=True)
    with pytest.raises(UnreadableDocumentError) as exc:
        _ingest(_image_bytes("JPEG"), "photo.jpg")
    assert "photo.jpg could not be read by AI vision" in str(exc.value)


def test_undecodable_bytes_fail_cleanly(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(UnreadableDocumentError):
        _ingest(b"not an image at all", "corrupt.png")


# --- prompt split: description-first for images, page prompt untouched ----------
def test_photo_without_text_ingests_on_description_alone(monkeypatch):
    # The original transcription prompt made the model answer
    # NO_LEGIBLE_CONTENT for a text-free photo, failing the upload. With the
    # description prompt, the description alone carries the chunk.
    description = (
        "1. DESCRIPTION: A track-mounted drill rig on a gravel pad beside an "
        "excavated test pit; spoil heap to the left, silt fence in the "
        "background.\n\n2. TEXT IN IMAGE: (no text visible)"
    )
    _setup(monkeypatch, transcript=description)
    docs, chunks_created, _, warning = _ingest(_image_bytes("JPEG"), "site.jpg")
    assert chunks_created == 1
    assert docs[0]["text"] == description
    assert docs[0]["visionDerived"] is True
    assert "was read by AI vision" in warning


def test_text_heavy_image_stores_description_and_transcription(monkeypatch):
    combined = (
        "1. DESCRIPTION: A software screenshot showing a settlement analysis "
        "window with a results table and a toolbar.\n\n"
        "2. TEXT IN IMAGE: SETTLEMENT ANALYSIS REPORT\nqc = 14.2 MPa\nSu = 48 kPa"
    )
    _setup(monkeypatch, transcript=combined)
    docs, *_ = _ingest(_image_bytes("PNG"), "screen.png")
    assert "DESCRIPTION" in docs[0]["text"]
    assert "SETTLEMENT ANALYSIS REPORT" in docs[0]["text"]


# The scanned-PDF-page prompt, pinned byte-for-byte: the image prompt split
# must never alter what PDF pages are transcribed with.
_ORIGINAL_PAGE_PROMPT = """You are transcribing one page of a scanned engineering document.

Transcribe all text you can actually read on this page, preserving headings, lists and table layout in plain text. If the page contains figures, charts, drawings or photographs, describe them factually: what they show, axis labels, units, legends, and clearly printed values.

Strict rules:
- Transcribe ONLY what is visibly legible. Never guess or infer numbers, symbols or words you cannot clearly read.
- Write [unreadable] in place of anything you cannot make out. Do not estimate it.
- Do not add commentary, interpretation or conclusions that are not printed on the page.
- If nothing on the page is legible, reply with exactly: NO_LEGIBLE_CONTENT"""


def test_page_prompt_byte_identical_after_split():
    assert vision_extraction.VISION_PAGE_PROMPT == _ORIGINAL_PAGE_PROMPT


def test_prompts_routed_to_the_right_calls(monkeypatch):
    sent = {}

    def spy_chat(png, prompt, model=None):
        sent[prompt[:40]] = prompt
        return "long enough output to clear the minimum"

    monkeypatch.setattr(vision_extraction, "_vision_chat", spy_chat)
    vision_extraction.transcribe_page_image(b"png")
    vision_extraction.describe_uploaded_image(b"png")
    prompts = list(sent.values())
    assert vision_extraction.VISION_PAGE_PROMPT in prompts
    assert vision_extraction.VISION_IMAGE_PROMPT in prompts
    assert len(prompts) == 2


def test_image_prompt_asks_for_both_parts_with_antifabrication_rules():
    p = vision_extraction.VISION_IMAGE_PROMPT
    assert "DESCRIPTION" in p and "TEXT IN IMAGE" in p
    # Anti-fabrication is the priority requirement.
    assert "Never name a specific brand, product, model, person, or place" in p
    assert "[unreadable]" in p
    assert "(no text visible)" in p
    # The refusal sentinel is restricted to nothing-at-all-describable.
    assert "Only if nothing at all can be described" in p
    assert p.rstrip().endswith("NO_LEGIBLE_CONTENT")


# --- req 4: the scope note names the image as an AI description -----------------
def _image_chunk(filename, cid="img0"):
    return {
        "id": cid,
        "filename": filename,
        "metadata": {"visionDerived": True, "fileType": ".jpg",
                     "visionModel": "test-vision-model"},
        # no pageStart at all
    }


def test_vision_scope_marks_image_entries():
    chunks = [
        _image_chunk("site-photo.jpg"),
        {"filename": "report.pdf", "metadata": {"visionDerived": True, "fileType": ".pdf"},
         "pageStart": 4},
    ]
    assert rag_service._vision_scope(chunks) == [
        {"filename": "report.pdf", "pages": [4], "image": False},
        {"filename": "site-photo.jpg", "pages": [], "image": True},
    ]


def _scope(**over):
    scope = {
        "searched": ["site-photo.jpg"], "grounded": ["site-photo.jpg"],
        "no_relevant": [], "excluded": [], "pending": [], "failed": [],
    }
    scope.update(over)
    return scope


def test_note_image_only_says_ai_description_not_document_text():
    note = _thread_scope_note(_scope(
        vision=[{"filename": "site-photo.jpg", "pages": [], "image": True}],
    ))
    assert "an AI vision description of the uploaded image site-photo.jpg" in note
    assert "model-generated interpretation, not document text." in note
    # It must NOT claim a scanned-page transcription.
    assert "scanned pages" not in note


def test_note_mixed_pages_and_image_names_both():
    note = _thread_scope_note(_scope(
        searched=["report.pdf", "site-photo.jpg"],
        grounded=["report.pdf", "site-photo.jpg"],
        vision=[
            {"filename": "report.pdf", "pages": [2, 3], "image": False},
            {"filename": "site-photo.jpg", "pages": [], "image": True},
        ],
    ))
    assert "AI vision transcription of scanned pages (report.pdf pages 2, 3)" in note
    assert "an AI vision description of the uploaded image site-photo.jpg" in note
    assert "not verbatim document text." in note


def test_pdf_page_note_wording_unchanged_by_image_support():
    note = _thread_scope_note(_scope(
        searched=["scan.pdf"], grounded=["scan.pdf"],
        vision=[{"filename": "scan.pdf", "pages": [2, 3], "image": False}],
    ))
    assert "AI vision transcription of scanned pages (scan.pdf pages 2, 3)" in note
    assert "not verbatim document text." in note


# --- req 5: non-image, non-document types stay rejected -------------------------
@pytest.mark.parametrize("enabled", [True, False])
def test_unsupported_type_rejected_regardless_of_flag(monkeypatch, enabled):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", enabled)
    with pytest.raises(HTTPException) as exc:
        files_router._validate_upload("payload.exe", 1000)
    assert exc.value.status_code == 400
    assert "Unsupported file type" in exc.value.detail


# --- req 6: flag off -> byte-identical to today ---------------------------------
def test_flag_off_webp_rejected_as_today(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        files_router._validate_upload("photo.webp", 1000)
    assert exc.value.status_code == 400


def test_flag_on_webp_accepted(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", True)
    files_router._validate_upload("photo.webp", 1000)  # no raise


def test_flag_off_png_rejected_upfront_without_ocr_exact_message(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(files_router, "tesseract_available", lambda: False)
    with pytest.raises(HTTPException) as exc:
        files_router._reject_unreadable_image("photo.png")
    assert exc.value.detail == (
        "photo.png is an image, and this server cannot read text from "
        "images (OCR is not available). Please upload a text document "
        "instead (PDF, DOCX, XLSX, XLS, CSV, PPTX)."
    )


def test_flag_on_png_not_rejected_without_ocr(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", True)
    monkeypatch.setattr(files_router, "tesseract_available", lambda: False)
    files_router._reject_unreadable_image("photo.png")  # no raise: vision reads it


def test_flag_off_ingest_takes_ocr_path_not_vision(monkeypatch):
    # With the flag off, an image upload must reach TODAY'S extractor
    # (extract_pages_from_image -> OCR or its exact error), with the vision
    # stub never called.
    seen = _setup(monkeypatch, enabled=False)
    monkeypatch.setattr(file_processing, "tesseract_available", lambda: False)
    with pytest.raises(UnreadableDocumentError) as exc:
        _ingest(_image_bytes("PNG"), "photo.png")
    assert seen["count"] == 0
    # Today's extractor message, verbatim ("not available" or "disabled"
    # depending on WHY tesseract_available() is False on this host).
    assert "photo.png is an image, and OCR is" in str(exc.value)
    assert "so no text can be read from it" in str(exc.value)


async def test_upload_config_flag_off_is_todays_static_list(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", False)
    out = await files_router.upload_config(current_user=object())
    assert out == {
        "extensions": [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".pptx"],
        "label": "PDF, DOCX, XLSX, XLS, CSV, PPTX",
    }


async def test_upload_config_flag_on_adds_vision_image_types(monkeypatch):
    monkeypatch.setattr(config, "VISION_EXTRACTION_ENABLED", True)
    out = await files_router.upload_config(current_user=object())
    assert out["extensions"][:6] == [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".pptx"]
    assert set(out["extensions"][6:]) == {".jpeg", ".jpg", ".png", ".webp"}
    assert out["label"].endswith("PNG, JPG, WEBP")


# --- req 7: mixed thread respects the per-document quota -------------------------
_DIM = 384
_QVEC = [1.0] + [0.0] * (_DIM - 1)


def _emb(cos):
    return [cos, math.sqrt(max(0.0, 1.0 - cos * cos))] + [0.0] * (_DIM - 2)


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


class _QueryVec:
    def embed(self, texts):
        return [_Vec(_QVEC) for _ in texts]


class _FakeReranker:
    def __init__(self, table):
        self.table = table

    def rerank(self, query, texts):
        return [self.table[t] for t in texts]


async def test_mixed_thread_pdf_plus_image_respects_per_doc_quota(monkeypatch):
    # 30-chunk PDF dominates cosine AND rerank; the image's single chunk sits
    # below the PDF's global top-5 but above the -11.0 threshold. The Phase 3
    # quota must (a) reserve it a candidate slot and (b) give its document a
    # context slot -- and the vision scope must name it as an image.
    docs, table = [], {}
    for i in range(30):
        text = f"PDF chunk {i} triaxial consolidation results"
        docs.append({
            "_id": f"pdf{i}", "category": "thread_upload", "chunkIndex": i,
            "filename": "report.pdf", "text": text, "embedding": _emb(0.9 - 0.004 * i),
            "metadata": {}, "threadId": TID, "userId": UID, "pageStart": i + 1,
        })
        table[text] = -9.0 - 0.05 * i
    img_text = "Photo description: rig on gravel pad, qc 14 MPa at 8 m"
    docs.append({
        "_id": "img0", "category": "thread_upload", "chunkIndex": 0,
        "filename": "site-photo.jpg", "text": img_text, "embedding": _emb(0.2),
        "metadata": {"visionDerived": True, "fileType": ".jpg",
                     "visionModel": "test-vision-model"},
        "threadId": TID, "userId": UID,  # no pageStart
    })
    table[img_text] = -10.5  # passes the -11.0 thread threshold

    monkeypatch.setattr(rag_service, "files_collection", _FakeFiles(docs))
    monkeypatch.setattr(rag_service, "get_embedding_model", lambda: _QueryVec())
    monkeypatch.setattr(rag_service, "RERANKER_ENABLED", True)
    monkeypatch.setattr(rag_service, "get_reranker", lambda: _FakeReranker(table))
    monkeypatch.setattr(config, "ROUTER_ENABLED", True)

    scope = {}
    kept = await rag_service.query_thread_documents("q", TID, UID, scope_out=scope)

    names = [c["filename"] for c in kept]
    assert "site-photo.jpg" in names, f"image crowded out of context: {names}"
    assert "report.pdf" in names
    assert scope["grounded"] == ["report.pdf", "site-photo.jpg"]
    assert scope["vision"] == [{"filename": "site-photo.jpg", "pages": [], "image": True}]
