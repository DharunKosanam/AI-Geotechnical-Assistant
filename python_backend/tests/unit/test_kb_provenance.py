"""Phase 1: provenance builder + helpers."""
from datetime import datetime

from app.services.kb_provenance import (
    PROVENANCE_FIELDS,
    build_provenance,
    source_format_of,
)


def test_build_provenance_has_exactly_the_declared_fields():
    prov = build_provenance(
        uploader_id="u1",
        uploader_name="Ada",
        uploaded_at=datetime(2026, 1, 1),
        project_tag="proj-x",
        doc_type="report",
        source_format="pdf",
        batch_id="b1",
        canonical_title="A Title",
        version=2,
        permission_confirmed=True,
    )
    assert set(prov.keys()) == set(PROVENANCE_FIELDS)
    assert prov["uploaderId"] == "u1"
    assert prov["version"] == 2
    assert prov["permissionConfirmed"] is True
    assert prov["projectTag"] == "proj-x"


def test_build_provenance_defaults():
    prov = build_provenance(
        uploader_id="u",
        uploader_name="n",
        uploaded_at=datetime(2026, 1, 1),
        project_tag=None,
        doc_type="reference",
        source_format="docx",
        batch_id="b",
        canonical_title="t",
    )
    assert prov["version"] == 1
    assert prov["permissionConfirmed"] is False
    assert prov["projectTag"] is None


def test_source_format_of():
    assert source_format_of("paper.PDF") == "pdf"
    assert source_format_of("notes.docx") == "docx"
    assert source_format_of("archive.tar.gz") == "gz"
    assert source_format_of("noext") == "unknown"
    assert source_format_of("") == "unknown"
    assert source_format_of(None) == "unknown"
