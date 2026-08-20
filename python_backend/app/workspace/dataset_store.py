"""MongoDB persistence for GeoPilot instrument datasets + parse jobs.

Two NEW collections (``workspace_datasets`` / ``workspace_parse_jobs``), never
the Chat-tab collections or the RAG pipeline. Every read and write is scoped by
``user_id`` so a user only ever sees their own datasets; a foreign/malformed id
simply matches nothing and returns None (clean 404 at the route).

A dataset document is a POINTER: parser id, dataset kind, metadata, array
shapes/dtypes, file paths, warnings, parent file id, job state, created
timestamp. Arrays live in the .npz on disk (see ``dataset_files``).

Job states: ``queued`` -> ``parsing`` -> ``parsed`` | ``failed``. ``progress``
is 0..100; ``error`` is set on failure. The dataset document mirrors
``status`` / ``progress`` / ``error`` so the panel list needs one query.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core import config
from app.core.database import (
    workspace_datasets_collection as datasets_collection,
    workspace_parse_jobs_collection as jobs_collection,
)
from app.workspace.dataset_files import json_safe

STATE_QUEUED = "queued"
STATE_PARSING = "parsing"
STATE_PARSED = "parsed"
STATE_FAILED = "failed"
ACTIVE_STATES = (STATE_QUEUED, STATE_PARSING)

LIST_LIMIT = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _as_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def dataset_badge(dataset_kind: str, label: str, metadata: Dict[str, Any]) -> str:
    """Compact "what was detected" text for the panel row, e.g.
    ``DFOS · 7795 gages`` / ``Pressure · 4 channels``."""
    if dataset_kind == "strain_distributed":
        n = metadata.get("n_gages")
        return f"{label} · {n} gages" if n is not None else label
    if dataset_kind == "pressure_timeseries":
        n = metadata.get("n_channels")
        return f"{label} · {n} channel{'s' if n != 1 else ''}" if n is not None else label
    return label


def effective_state(doc: Dict[str, Any]) -> tuple[str, Optional[str]]:
    """Read-time staleness rule (mirrors the ingest lifecycle derivation): a
    job still queued/parsing with no update for INSTRUMENT_PARSE_TIMEOUT_SECONDS
    is reported failed with an "interrupted" reason so the row offers a retry
    instead of spinning forever after a backend restart mid-parse."""
    state = doc.get("status") or doc.get("state") or STATE_QUEUED
    error = doc.get("error")
    if state in ACTIVE_STATES:
        ref = _as_utc(doc.get("updated_at")) or _as_utc(doc.get("created_at"))
        if ref is not None:
            age = (_now() - ref).total_seconds()
            if age > config.INSTRUMENT_PARSE_TIMEOUT_SECONDS:
                return STATE_FAILED, (
                    "Parsing was interrupted (no progress for "
                    f"{int(age)} s) - the backend may have restarted. Retry the parse."
                )
    return state, error


# --- Public shapes -----------------------------------------------------------
def _public_dataset(doc: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    state, error = effective_state(doc)
    metadata = dict(doc.get("metadata") or {})
    raw_header = metadata.pop("_raw_header", None)
    if full and raw_header is not None:
        metadata["_raw_header"] = raw_header
    out = {
        "id": str(doc["_id"]),
        "kind": "dataset",
        "filename": doc.get("filename"),
        "size_bytes": doc.get("size_bytes"),
        "parser_id": doc.get("parser_id"),
        "dataset_kind": doc.get("dataset_kind"),
        "label": doc.get("label"),
        "badge": dataset_badge(doc.get("dataset_kind", ""), doc.get("label", ""), metadata),
        "status": state,
        "progress": int(doc.get("progress") or 0) if state != STATE_PARSED else 100,
        "error": error,
        "job_id": doc.get("job_id"),
        "metadata": metadata,
        "shapes": doc.get("shapes") or {},
        "dtypes": doc.get("dtypes") or {},
        "warnings": doc.get("warnings") or [],
        "segments": doc.get("segments") or [],
        "parent_file_id": doc.get("parent_file_id"),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "parsed_at": _iso(doc.get("parsed_at")),
    }
    if full:
        out["npz_path"] = doc.get("npz_path")
        out["raw_path"] = doc.get("raw_path")
        out["parse_elapsed_s"] = doc.get("parse_elapsed_s")
    return out


def _public_job(doc: Dict[str, Any]) -> Dict[str, Any]:
    state, error = effective_state(doc)
    return {
        "id": str(doc["_id"]),
        "dataset_id": doc.get("dataset_id"),
        "state": state,
        "progress": int(doc.get("progress") or 0) if state != STATE_PARSED else 100,
        "error": error,
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "started_at": _iso(doc.get("started_at")),
        "finished_at": _iso(doc.get("finished_at")),
        "elapsed_s": doc.get("elapsed_s"),
    }


# --- Create ------------------------------------------------------------------
async def create_dataset(
    user_id: str,
    *,
    filename: str,
    size_bytes: int,
    parser_id: str,
    dataset_kind: str,
    label: str,
    raw_path: str,
    parent_file_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert the dataset pointer (queued) + its job; returns {dataset_id, job_id}."""
    now = _now()
    ds_doc = {
        "user_id": user_id,
        "filename": filename,
        "size_bytes": int(size_bytes),
        "parser_id": parser_id,
        "dataset_kind": dataset_kind,
        "label": label,
        "status": STATE_QUEUED,
        "progress": 0,
        "error": None,
        "job_id": None,
        "metadata": {},
        "shapes": {},
        "dtypes": {},
        "warnings": [],
        "segments": [],
        "npz_path": None,
        "raw_path": raw_path,
        "parent_file_id": parent_file_id,
        "created_at": now,
        "updated_at": now,
        "parsed_at": None,
    }
    res = await datasets_collection.insert_one(ds_doc)
    dataset_id = str(res.inserted_id)
    job_id = await create_job(user_id, dataset_id)
    await datasets_collection.update_one(
        {"_id": res.inserted_id, "user_id": user_id}, {"$set": {"job_id": job_id}}
    )
    return {"dataset_id": dataset_id, "job_id": job_id}


async def create_job(user_id: str, dataset_id: str) -> str:
    now = _now()
    res = await jobs_collection.insert_one(
        {
            "user_id": user_id,
            "dataset_id": dataset_id,
            "state": STATE_QUEUED,
            "progress": 0,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "elapsed_s": None,
        }
    )
    return str(res.inserted_id)


# --- Job transitions -----------------------------------------------------------
async def _set_both(
    user_id: str, dataset_id: str, job_id: str, job_set: Dict[str, Any], ds_set: Dict[str, Any]
) -> None:
    now = _now()
    jid, did = _oid(job_id), _oid(dataset_id)
    if jid is not None:
        await jobs_collection.update_one(
            {"_id": jid, "user_id": user_id}, {"$set": {**job_set, "updated_at": now}}
        )
    if did is not None:
        await datasets_collection.update_one(
            {"_id": did, "user_id": user_id}, {"$set": {**ds_set, "updated_at": now}}
        )


async def mark_parsing(user_id: str, dataset_id: str, job_id: str) -> None:
    now = _now()
    await _set_both(
        user_id, dataset_id, job_id,
        {"state": STATE_PARSING, "progress": 0, "started_at": now, "error": None},
        {"status": STATE_PARSING, "progress": 0, "error": None},
    )


async def update_progress(user_id: str, dataset_id: str, job_id: str, percent: int) -> None:
    pct = max(0, min(99, int(percent)))
    await _set_both(
        user_id, dataset_id, job_id, {"progress": pct}, {"progress": pct}
    )


async def mark_parsed(
    user_id: str,
    dataset_id: str,
    job_id: str,
    *,
    metadata: Dict[str, Any],
    shapes: Dict[str, Any],
    dtypes: Dict[str, str],
    warnings: List[str],
    npz_path: str,
    elapsed_s: float,
) -> None:
    now = _now()
    await _set_both(
        user_id, dataset_id, job_id,
        {"state": STATE_PARSED, "progress": 100, "finished_at": now, "elapsed_s": elapsed_s, "error": None},
        {
            "status": STATE_PARSED,
            "progress": 100,
            "error": None,
            "metadata": json_safe(metadata),
            "shapes": {k: list(v) for k, v in shapes.items()},
            "dtypes": dict(dtypes),
            "warnings": list(warnings),
            "npz_path": npz_path,
            "parsed_at": now,
            "parse_elapsed_s": elapsed_s,
        },
    )


async def mark_failed(user_id: str, dataset_id: str, job_id: str, error: str) -> None:
    now = _now()
    await _set_both(
        user_id, dataset_id, job_id,
        {"state": STATE_FAILED, "finished_at": now, "error": error},
        {"status": STATE_FAILED, "error": error},
    )


async def requeue(user_id: str, dataset_id: str) -> Optional[str]:
    """Reset a dataset for a retry: new job, status queued. Returns the job id
    or None if the dataset is not this user's."""
    did = _oid(dataset_id)
    if did is None:
        return None
    doc = await datasets_collection.find_one({"_id": did, "user_id": user_id})
    if doc is None:
        return None
    job_id = await create_job(user_id, dataset_id)
    await datasets_collection.update_one(
        {"_id": did, "user_id": user_id},
        {"$set": {"status": STATE_QUEUED, "progress": 0, "error": None, "job_id": job_id, "updated_at": _now()}},
    )
    return job_id


async def set_segments(user_id: str, dataset_id: str, segments: List[Dict[str, Any]]) -> bool:
    """Attach detected segments/events (from a calculator run) to the dataset."""
    did = _oid(dataset_id)
    if did is None:
        return False
    res = await datasets_collection.update_one(
        {"_id": did, "user_id": user_id},
        {"$set": {"segments": json_safe(segments), "updated_at": _now()}},
    )
    return bool(getattr(res, "matched_count", 0))


# --- Reads -------------------------------------------------------------------
async def get_dataset_doc(user_id: str, dataset_id: str) -> Optional[Dict[str, Any]]:
    did = _oid(dataset_id)
    if did is None:
        return None
    return await datasets_collection.find_one({"_id": did, "user_id": user_id})


async def get_dataset(user_id: str, dataset_id: str, full: bool = False) -> Optional[Dict[str, Any]]:
    doc = await get_dataset_doc(user_id, dataset_id)
    return _public_dataset(doc, full=full) if doc else None


async def list_datasets(user_id: str, limit: int = LIST_LIMIT) -> List[Dict[str, Any]]:
    """This user's datasets, newest first (compact metadata, no raw header)."""
    cursor = datasets_collection.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [_public_dataset(d) for d in docs]


async def latest_parsed_of_kind(user_id: str, dataset_kind: str) -> List[Dict[str, Any]]:
    """Parsed datasets of a kind, newest first (RAW docs incl. npz_path) --
    what a dataset-bound calculator draws from."""
    cursor = (
        datasets_collection.find({"user_id": user_id, "dataset_kind": dataset_kind, "status": STATE_PARSED})
        .sort("created_at", -1)
        .limit(LIST_LIMIT)
    )
    return await cursor.to_list(length=LIST_LIMIT)


async def get_job(user_id: str, job_id: str) -> Optional[Dict[str, Any]]:
    jid = _oid(job_id)
    if jid is None:
        return None
    doc = await jobs_collection.find_one({"_id": jid, "user_id": user_id})
    return _public_job(doc) if doc else None


async def delete_dataset(user_id: str, dataset_id: str) -> Optional[Dict[str, Any]]:
    """Delete the pointer doc (+ its jobs); returns the removed doc so the
    caller can delete the files, or None if not found / not this user's."""
    did = _oid(dataset_id)
    if did is None:
        return None
    doc = await datasets_collection.find_one({"_id": did, "user_id": user_id})
    if doc is None:
        return None
    await datasets_collection.delete_one({"_id": did, "user_id": user_id})
    await jobs_collection.delete_many({"dataset_id": dataset_id, "user_id": user_id})
    return doc
