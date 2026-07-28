"""KB upload provenance schema (Phase 1).

Every knowledge-base document (any parent metadata doc and every chunk) carries a
provenance block so a bad upload can be located and removed cheaply, and so
citations can render a canonical title independent of the raw filename.

This module ONLY defines the schema, a builder, and the legacy-backfill
defaults. Stamping provenance onto NEW uploads happens in the KB upload endpoint
(Phase 4); backfilling EXISTING KB docs happens in
scripts/kb_backfill_provenance.py. Nothing here changes retrieval or ingest.
"""
import os
from datetime import datetime
from typing import Any, Dict, Optional

# The provenance field names, defined once so backfill, stamping, admin listing
# and retrieval-surfacing all agree on the exact set.
PROVENANCE_FIELDS = (
    "uploaderId",
    "uploaderName",
    "uploadedAt",
    "projectTag",
    "docType",
    "sourceFormat",
    "batchId",
    "canonicalTitle",
    "version",
    "permissionConfirmed",
)

# Sentinels for the one-time backfill of pre-provenance KB material.
LEGACY_UPLOADER_ID = "system"
LEGACY_UPLOADER_NAME = "Legacy KB Import"
LEGACY_PROJECT_TAG = "legacy"
LEGACY_DOC_TYPE = "reference"
LEGACY_BATCH_ID = "legacy-backfill"


def source_format_of(filename: Optional[str]) -> str:
    """Lowercased file extension without the dot (e.g. 'pdf'), or 'unknown'.

    Used to fill sourceFormat when the doc has no metadata.fileType (true for the
    legacy KB corpus).
    """
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    return ext or "unknown"


def build_provenance(
    *,
    uploader_id: str,
    uploader_name: str,
    uploaded_at: datetime,
    project_tag: Optional[str],
    doc_type: str,
    source_format: str,
    batch_id: str,
    canonical_title: str,
    version: int = 1,
    permission_confirmed: bool = False,
) -> Dict[str, Any]:
    """Assemble a provenance block. Keys match PROVENANCE_FIELDS exactly.

    Stamped identically on the parent doc and every chunk of an upload so any
    single chunk is traceable to its uploader / project / batch without a join.
    """
    prov = {
        "uploaderId": uploader_id,
        "uploaderName": uploader_name,
        "uploadedAt": uploaded_at,
        "projectTag": project_tag,
        "docType": doc_type,
        "sourceFormat": source_format,
        "batchId": batch_id,
        "canonicalTitle": canonical_title,
        "version": version,
        "permissionConfirmed": permission_confirmed,
    }
    # Guard against drift between the builder and the declared field set.
    assert set(prov.keys()) == set(PROVENANCE_FIELDS)
    return prov
