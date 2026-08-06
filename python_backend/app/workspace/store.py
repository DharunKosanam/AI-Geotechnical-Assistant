"""In-memory, per-user session store for the GeoPilot workspace.

Uploaded GeoPilot documents are the pool the calculators draw from. They are
NOT knowledge-base material: a ``.CPT`` sounding is raw engineering data the
deterministic calculator reads verbatim, not something to embed for RAG. So
they live here — a process-local, per-user store keyed by the authenticated
user id — completely separate from the chat upload / vector pipeline.

Also holds the most recent deterministic result per user (keyed by a result
id) so the Excel exporter can rebuild the workbook from the SAME structured
object the calculator returned, without re-parsing the file or the AI text.

Scope + lifetime: this is intentionally ephemeral (a working session), bounded
per user so it can't grow without limit, and reset on process restart. It never
touches Mongo, Redis, auth or the RAG stack.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

# Keep memory bounded: only the most recent N documents / results per user are
# retained; older ones are evicted oldest-first.
MAX_DOCS_PER_USER = 25
MAX_RESULTS_PER_USER = 25

_lock = threading.Lock()
# {user_id: {doc_id: SessionDocument}} — insertion-ordered per user.
_docs: Dict[str, "Dict[str, SessionDocument]"] = {}
# {user_id: {result_id: export_payload}} — insertion-ordered per user.
_results: Dict[str, Dict[str, Dict[str, Any]]] = {}
# Monotonic sequence so "most recent" ordering is stable even within one clock tick.
_seq = itertools.count(1)


@dataclass
class SessionDocument:
    """One document uploaded into the current GeoPilot session."""

    id: str
    filename: str
    extension: str  # lowercased, includes the dot, e.g. ".cpt"
    text: str
    size: int
    uploaded_at: datetime
    seq: int

    def public(self) -> Dict[str, Any]:
        """The metadata the frontend keeps in component state (no file body)."""
        return {
            "id": self.id,
            "filename": self.filename,
            "extension": self.extension,
            "status": "ready",
        }


def _ext_of(filename: str) -> str:
    i = filename.rfind(".")
    return filename[i:].lower() if i >= 0 else ""


def _evict(bucket: Dict[str, Any], limit: int) -> None:
    """Drop oldest entries (insertion order) until at most ``limit`` remain."""
    while len(bucket) > limit:
        oldest_key = next(iter(bucket))
        del bucket[oldest_key]


# --- Documents -------------------------------------------------------------
def add_document(user_id: str, filename: str, text: str) -> SessionDocument:
    """Store a document for a user and return its record."""
    doc = SessionDocument(
        id=str(uuid4()),
        filename=filename,
        extension=_ext_of(filename),
        text=text,
        size=len(text.encode("utf-8", errors="replace")),
        uploaded_at=datetime.now(timezone.utc),
        seq=next(_seq),
    )
    with _lock:
        bucket = _docs.setdefault(user_id, {})
        bucket[doc.id] = doc
        _evict(bucket, MAX_DOCS_PER_USER)
    return doc


def list_documents(user_id: str) -> List[SessionDocument]:
    """All of a user's session documents, most-recent first."""
    with _lock:
        docs = list(_docs.get(user_id, {}).values())
    return sorted(docs, key=lambda d: d.seq, reverse=True)


def get_document(user_id: str, doc_id: str) -> Optional[SessionDocument]:
    with _lock:
        return _docs.get(user_id, {}).get(doc_id)


def remove_document(user_id: str, doc_id: str) -> bool:
    """Remove a document; True if it existed."""
    with _lock:
        bucket = _docs.get(user_id, {})
        return bucket.pop(doc_id, None) is not None


def latest_document_with_extension(
    user_id: str, extension: str
) -> Optional[SessionDocument]:
    """The most recently uploaded document whose extension matches (e.g. ".cpt")."""
    want = extension.lower()
    for doc in list_documents(user_id):  # already most-recent first
        if doc.extension == want:
            return doc
    return None


# --- Deterministic results (for Excel export) ------------------------------
def store_result(user_id: str, payload: Dict[str, Any]) -> str:
    """Stash a deterministic result payload; return its id for later export."""
    result_id = str(uuid4())
    with _lock:
        bucket = _results.setdefault(user_id, {})
        bucket[result_id] = payload
        _evict(bucket, MAX_RESULTS_PER_USER)
    return result_id


def get_result(user_id: str, result_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _results.get(user_id, {}).get(result_id)


def latest_result(user_id: str) -> Optional[Dict[str, Any]]:
    """The most recently stored result payload for a user (None if none yet).

    Used to scope free-text Q&A to the current in-session result. Relies on the
    per-user bucket being insertion-ordered (dict order), so the last inserted
    entry is the newest.
    """
    with _lock:
        bucket = _results.get(user_id, {})
        if not bucket:
            return None
        return bucket[next(reversed(bucket))]


def clear_user(user_id: str) -> None:
    """Drop all of a user's session state (used by tests)."""
    with _lock:
        _docs.pop(user_id, None)
        _results.pop(user_id, None)
