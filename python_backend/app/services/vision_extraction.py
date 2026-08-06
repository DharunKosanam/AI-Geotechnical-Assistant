"""
Vision-based extraction for PDF pages with no text layer, and for directly
uploaded images (JPEG/PNG/WebP).

Runs ONLY inside the background ingestion path (a worker thread), never on the
request path. PDF pages: only those the normal extraction already identified
as having no readable text, each rasterized with PyMuPDF at a capped DPI and
sent to the vision-capable Ollama model ONE PAGE PER CALL -- image tokens are
expensive against the configured num_ctx, so pages are never batched. Direct
images: downscaled to VISION_IMAGE_MAX_DIM and sent as one call.

The output is model-generated INTERPRETATION, not extracted text. Callers must
carry the provenance flag (visionDerived) on every chunk built from it; this
module only produces the raw transcripts.
"""
import io
import time
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import ollama
from PIL import Image, ImageOps

from app.core import config

# Transcribe-don't-guess prompt for SCANNED PDF PAGES. The model must state
# when content is unclear rather than inventing values it cannot read -- a
# hallucinated number in a geotechnical report is worse than an unindexed
# page. A scanned page IS a document, so pure transcription framing is
# correct here; direct image uploads use VISION_IMAGE_PROMPT below.
VISION_PAGE_PROMPT = """You are transcribing one page of a scanned engineering document.

Transcribe all text you can actually read on this page, preserving headings, lists and table layout in plain text. If the page contains figures, charts, drawings or photographs, describe them factually: what they show, axis labels, units, legends, and clearly printed values.

Strict rules:
- Transcribe ONLY what is visibly legible. Never guess or infer numbers, symbols or words you cannot clearly read.
- Write [unreadable] in place of anything you cannot make out. Do not estimate it.
- Do not add commentary, interpretation or conclusions that are not printed on the page.
- If nothing on the page is legible, reply with exactly: NO_LEGIBLE_CONTENT"""

# Description-first prompt for DIRECTLY UPLOADED IMAGES. An uploaded image is
# often not a document at all (a site photo, equipment, a screenshot), so the
# model is asked for BOTH a factual description and a transcription of any
# visible text -- and an image with no text must still ingest on description
# alone. Anti-fabrication is the priority: the transcription prompt above,
# applied to a screenshot, produced a confidently wrong product name while
# ignoring the visible heading; a hedged description is a correct answer, a
# confident wrong name is not. The refusal sentinel here applies ONLY to an
# image where nothing at all can be described (blank/corrupt/fully obscured).
VISION_IMAGE_PROMPT = """You are describing an image uploaded to an engineering assistant. The image may be a site photo, equipment, a screenshot, a chart, a drawing, or a photo of a document.

Provide both parts:

1. DESCRIPTION: State factually what the image shows -- the subject, the setting, notable objects, and their spatial arrangement. Describe charts and drawings by what is visibly plotted or drawn (axes, labels, units, legends, visible trends).

2. TEXT IN IMAGE: Transcribe all text visible in the image exactly as it appears, preserving headings, labels and table layout in plain text. If no text is visible, write exactly: (no text visible)

Strict rules -- accuracy over completeness:
- Describe ONLY what is visibly present. Do not infer what is outside the frame, and do not guess what the image is "probably" showing.
- Transcribe text exactly as written. Never complete, correct, or infer wording you cannot clearly read; write [unreadable] in its place.
- Never name a specific brand, product, model, person, or place unless it is clearly legible or unmistakable in the image itself. Prefer "a track-mounted drill rig" over a guessed manufacturer. Stating uncertainty is a correct answer; a confident wrong name is not.
- Do not add conclusions or interpretation beyond what is shown.
- Only if nothing at all can be described -- the image is blank, corrupt, or fully obscured -- reply with exactly: NO_LEGIBLE_CONTENT"""

# A transcript shorter than this is treated as "the model read nothing usable"
# and the page stays unindexed (mirrors OCR_MIN_TEXT_LEN's role for OCR).
MIN_TRANSCRIPT_LEN = 20


def render_page_png(file_content: bytes, page_number: int, dpi: int) -> bytes:
    """Rasterize one 1-indexed PDF page to PNG bytes at ``dpi``."""
    doc = None
    try:
        doc = fitz.open(stream=file_content, filetype="pdf")
        zoom = dpi / 72.0
        pix = doc[page_number - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def _vision_chat(png_bytes: bytes, prompt: str, model: Optional[str] = None) -> str:
    """One blocking vision call for ONE image with the given prompt. Returns
    the model's text, or "" when it reported nothing legible/describable
    (the NO_LEGIBLE_CONTENT sentinel both prompts define). Raises on
    transport/model errors -- callers isolate failures. Identical client,
    options and refusal handling for both prompts, so the PDF-page path is
    byte-identical to before the split."""
    client = ollama.Client(
        host=config.OLLAMA_BASE_URL, timeout=config.VISION_TIMEOUT_SECONDS
    )
    resp = client.chat(
        model=model or config.VISION_MODEL,
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [png_bytes],
        }],
        think=False,
        options={
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": config.OLLAMA_NUM_PREDICT,
            # Transcription wants fidelity, not creativity.
            "temperature": 0.0,
        },
    )
    text = ((resp.get("message") or {}).get("content") or "").strip()
    if not text or "NO_LEGIBLE_CONTENT" in text:
        return ""
    return text


def transcribe_page_image(png_bytes: bytes, model: Optional[str] = None) -> str:
    """Scanned-PDF-page transcription (VISION_PAGE_PROMPT). Returns the
    transcript, or "" when the model reported nothing legible."""
    return _vision_chat(png_bytes, VISION_PAGE_PROMPT, model)


def describe_uploaded_image(png_bytes: bytes, model: Optional[str] = None) -> str:
    """Direct-image description + text transcription (VISION_IMAGE_PROMPT).
    Returns the combined output, or "" only when the model could describe
    nothing at all (blank/corrupt/fully obscured image)."""
    return _vision_chat(png_bytes, VISION_IMAGE_PROMPT, model)


def extract_vision_pages(
    file_content: bytes,
    page_numbers: List[int],
    filename: str = "document",
) -> Dict[str, Any]:
    """Vision-transcribe the given no-text-layer pages, one call per page.

    Applies VISION_MAX_PAGES_PER_DOC: pages beyond the cap are never attempted.
    A failure on one page is recorded and the next page proceeds -- a single
    bad page can never fail the whole ingest.

    Returns a dict:
        pages         -- [(page_number, transcript)] for pages that produced
                         usable text, in page order
        failed_pages  -- attempted pages with no usable result (error, timeout,
                         or the model reported nothing legible)
        skipped_pages -- pages beyond the per-document cap, never attempted
        model         -- the vision model name used
        seconds       -- total wall time spent in vision calls
    """
    cap = max(0, config.VISION_MAX_PAGES_PER_DOC)
    ordered = sorted(page_numbers)
    attempt, skipped = ordered[:cap], ordered[cap:]
    model = config.VISION_MODEL

    pages: List[Tuple[int, str]] = []
    failed: List[int] = []
    started = time.monotonic()

    if skipped:
        print(
            f"      [VISION] {filename}: cap {cap} reached -- "
            f"{len(skipped)} page(s) not attempted"
        )

    for pn in attempt:
        page_started = time.monotonic()
        try:
            png = render_page_png(file_content, pn, config.VISION_DPI)
            transcript = transcribe_page_image(png, model=model)
            elapsed = time.monotonic() - page_started
            if len(transcript) >= MIN_TRANSCRIPT_LEN:
                pages.append((pn, transcript))
                print(
                    f"      [VISION] Page {pn}: {len(transcript)} chars "
                    f"in {elapsed:.1f}s ({model})"
                )
            else:
                failed.append(pn)
                print(
                    f"      [VISION] Page {pn}: nothing legible "
                    f"({elapsed:.1f}s) -- page stays unindexed"
                )
        except Exception as e:
            failed.append(pn)
            print(f"      [VISION ERROR] Page {pn}: {e} -- page stays unindexed")

    return {
        "pages": pages,
        "failed_pages": failed,
        "skipped_pages": skipped,
        "model": model,
        "seconds": time.monotonic() - started,
    }


# ---------------------------------------------------------------------------
# Direct image upload (JPEG/PNG/WebP)
# ---------------------------------------------------------------------------
def prepare_image_for_vision(content: bytes, filename: str = "image") -> bytes:
    """Decode an uploaded image and re-encode it as PNG for the vision call,
    downscaling to VISION_IMAGE_MAX_DIM on the longest edge first. Oversized
    photos are shrunk, never rejected; EXIF rotation is applied so a phone
    photo arrives upright. Raises ValueError when the bytes are not a
    decodable image."""
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception as e:
        raise ValueError(f"{filename} could not be decoded as an image: {e}")
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    max_dim = max(1, config.VISION_IMAGE_MAX_DIM)
    if longest > max_dim:
        scale = max_dim / longest
        img = img.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))),
            Image.LANCZOS,
        )
        print(f"      [VISION] {filename}: downscaled {w}x{h} -> {img.size[0]}x{img.size[1]}")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def describe_image(content: bytes, filename: str = "image") -> Dict[str, Any]:
    """ONE vision call for a directly uploaded image (same timeout and
    options as the scanned-page path; no rasterization step) using the
    DESCRIPTION prompt -- a photo with no text ingests successfully on its
    description alone.

    Returns {"text", "model", "seconds"} on success. Raises
    UnreadableDocumentError -- the upload fails with that user-facing reason
    and indexes NOTHING -- when the call fails or the model could describe
    nothing at all (a blank, corrupt, or fully obscured image): an image
    document has no verbatim layer to fall back on, so an empty or refusal
    output must never be stored as a chunk."""
    from app.services.file_processing import UnreadableDocumentError

    model = config.VISION_MODEL
    started = time.monotonic()
    try:
        png = prepare_image_for_vision(content, filename)
        transcript = describe_uploaded_image(png, model=model)
    except Exception as e:
        print(f"      [VISION ERROR] {filename}: {e}")
        raise UnreadableDocumentError(
            f"{filename} could not be read by AI vision ({e}). Please try a "
            f"clearer image or upload the source document instead."
        )
    elapsed = time.monotonic() - started
    if len(transcript) < MIN_TRANSCRIPT_LEN:
        print(f"      [VISION] {filename}: nothing describable ({elapsed:.1f}s)")
        raise UnreadableDocumentError(
            f"AI vision could not make out any content in {filename} -- the "
            f"image may be blank, corrupt, or too obscured to interpret. "
            f"Please try a clearer image."
        )
    print(f"      [VISION] {filename}: {len(transcript)} chars in {elapsed:.1f}s ({model})")
    return {"text": transcript, "model": model, "seconds": elapsed}
