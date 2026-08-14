"""Diagram upload wiring (Phase 3) + scope-note labeling (Phase 4).

Covers:
  1. the diagram ingestion branch feeds the Phase 2 extractor's output into
     ingest_document via the pre-extracted-pages hook with
     provenance={"sourceType": "diagram"} — the vision branch requires
     pre_extracted_pages to be None, so a diagram can never reach the vision
     model — and flips the parent doc to "processed";
  2. an EMPTY diagram fails the parent doc with the extractor's user-facing
     message (what the chip's status poll surfaces), and indexes nothing;
  3. _validate_diagram_upload entry rules: .png only, real PNG magic, XML
     required, same size/emptiness limits as _validate_upload;
  4. the scope note labels a drawn diagram as "the diagram <name>" (and the
     collective noun becomes "sources"), while a diagram-free thread renders
     byte-identical to today's wording.

Deterministic: ingest_document/files_collection/ingest_release faked, no DB,
no network, no LLM.
"""

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.routers import files as files_router
from app.routers.chat import _thread_scope_note
from app.services.diagram_extraction import extract_diagram_text

pytestmark = pytest.mark.unit

_XML = (
    '<mxfile><diagram id="d1" name="Flow"><mxGraphModel><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="2" value="A" vertex="1" parent="1"/>'
    '<mxCell id="3" value="B" vertex="1" parent="1"/>'
    '<mxCell id="4" edge="1" parent="1" source="2" target="3"/>'
    "</root></mxGraphModel></diagram></mxfile>"
)
_EMPTY_XML = (
    '<mxfile><diagram id="d1" name="Blank"><mxGraphModel><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    "</root></mxGraphModel></diagram></mxfile>"
)
_PNG = b"\x89PNG\r\n\x1a\n" + b"fakebody"


class _FakeCollection:
    def __init__(self):
        self.updates = []

    async def update_one(self, query, update):
        self.updates.append((query, update))


def _wire(monkeypatch):
    """Fake the Mongo collection + queue slot; return the capture points."""
    coll = _FakeCollection()
    monkeypatch.setattr(files_router, "files_collection", coll)
    monkeypatch.setattr(files_router, "ingest_release", lambda: None)
    return coll


async def test_diagram_branch_uses_extractor_output_and_provenance(monkeypatch):
    coll = _wire(monkeypatch)
    captured = {}

    async def fake_ingest(filename, file_content, category, user_id=None,
                          thread_id=None, pre_extracted_pages=None, provenance=None):
        captured.update(
            filename=filename, category=category, user_id=user_id,
            thread_id=thread_id, pre_extracted_pages=pre_extracted_pages,
            provenance=provenance,
        )
        return {"chunks_created": 1}

    monkeypatch.setattr(files_router, "ingest_document", fake_ingest)
    await files_router.process_file_ingestion(
        "flow-ab12cd.png", _PNG, "thread_upload",
        parent_id=ObjectId(), user_id="u1", thread_id="t1",
        source_type="diagram", diagram_xml=_XML,
    )
    # The indexed text IS the Phase 2 extractor's output, via the
    # pre-extracted-pages hook (which also locks the vision branch out).
    assert captured["pre_extracted_pages"] == [(1, extract_diagram_text(_XML), False)]
    assert captured["provenance"] == {"sourceType": "diagram"}
    assert captured["category"] == "thread_upload"
    assert captured["thread_id"] == "t1"
    assert coll.updates[-1][1]["$set"]["status"] == "processed"


async def test_empty_diagram_fails_parent_with_user_facing_message(monkeypatch):
    coll = _wire(monkeypatch)

    async def boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("ingest_document must not run for an empty diagram")

    monkeypatch.setattr(files_router, "ingest_document", boom)
    await files_router.process_file_ingestion(
        "blank-zz99xx.png", _PNG, "thread_upload",
        parent_id=ObjectId(), user_id="u1", thread_id="t1",
        source_type="diagram", diagram_xml=_EMPTY_XML,
    )
    q, update = coll.updates[-1]
    assert update["$set"]["status"] == "failed"
    # The extractor's message passes through VERBATIM — it is what the chip
    # shows after the status poll reports the failure.
    assert "no shapes" in update["$set"]["error"]


def test_validate_diagram_upload_entry_rules():
    ok = dict(filename="d-a1b2c3.png", size_bytes=len(_PNG),
              file_content=_PNG, diagram_xml=_XML)
    files_router._validate_diagram_upload(**ok)  # valid: no raise

    with pytest.raises(HTTPException) as exc:
        files_router._validate_diagram_upload(**{**ok, "filename": "d.svg"})
    assert "must be a .png" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        files_router._validate_diagram_upload(**{**ok, "file_content": b"GIF89a"})
    assert "not a valid PNG" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        files_router._validate_diagram_upload(**{**ok, "diagram_xml": "   "})
    assert "source XML" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        files_router._validate_diagram_upload(**{**ok, "size_bytes": 0})
    assert exc.value.detail == "File is empty"


def _scope(**overrides):
    base = {
        "searched": ["report.pdf", "flow-ab12cd.png"],
        "grounded": ["report.pdf", "flow-ab12cd.png"],
        "no_relevant": [],
        "excluded": [],
        "pending": [],
        "failed": [],
    }
    base.update(overrides)
    return base


def test_scope_note_labels_diagram_and_swaps_collective_noun():
    note = _thread_scope_note(_scope(diagram_files=["flow-ab12cd.png"]))
    assert "Searched 2 attached sources" in note
    assert "the diagram flow-ab12cd.png" in note
    # The document itself renders exactly as before -- bare filename.
    assert "report.pdf" in note
    assert "the diagram report.pdf" not in note


def test_scope_note_without_diagrams_is_byte_identical():
    # Absent key and empty list must both render today's wording exactly.
    plain = _thread_scope_note(_scope())
    with_empty_key = _thread_scope_note(_scope(diagram_files=[]))
    assert plain == with_empty_key
    assert "Searched 2 attached documents: report.pdf and flow-ab12cd.png." in plain
    assert "diagram" not in plain.replace("flow-ab12cd.png", "")


def test_scope_note_pending_diagram_is_labeled():
    note = _thread_scope_note(_scope(
        searched=["report.pdf", "spec.pdf"],
        grounded=["report.pdf"],
        pending=["flow-ab12cd.png"],
        diagram_files=["flow-ab12cd.png"],
    ))
    assert "the diagram flow-ab12cd.png is still being processed" in note
