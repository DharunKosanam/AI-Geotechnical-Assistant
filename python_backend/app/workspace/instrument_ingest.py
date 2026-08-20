"""Instrument-file ingestion for GeoPilot uploads (INSTRUMENT_PARSERS_ENABLED).

Two halves:

* :func:`ingest_upload` -- the branch the workspace upload route takes when
  ``registry.sniff`` matched the first 2 KB of an upload. It streams the rest
  of the file to disk (retained raw upload), creates the dataset pointer + job
  documents (``queued``) and schedules :func:`run_parse_job` as a FastAPI
  background task. The request returns immediately; NOTHING is embedded.
* :func:`run_parse_job` -- the background job. Runs the parser in a small
  dedicated thread pool (a parse is CPU-bound Python; the event loop must stay
  free), reports throttled progress into Mongo, writes the arrays as one
  compressed ``.npz`` and finalises the pointer document (``parsed`` with
  metadata / shapes / warnings, or ``failed`` with a user-readable error).

Mirrors the existing ingest offload pattern (``rag_service._get_ingest_pool``):
BackgroundTasks + bounded ThreadPoolExecutor + a status document.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, HTTPException, UploadFile, status

from app.core import config
from app.workspace import dataset_files, dataset_store
from app.workspace.parsers import registry
from app.workspace.parsers.base import ParserError

logger = logging.getLogger(__name__)

# Stream the upload to disk in 1 MB pieces; the first SNIFF_BYTES were read by
# the route for sniffing and are handed in as ``head``.
_CHUNK = 1024 * 1024
# Progress writes to Mongo are throttled to at most one per this interval.
_PROGRESS_MIN_INTERVAL_S = 0.5

_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(
                    max_workers=max(1, config.INSTRUMENT_PARSE_WORKERS),
                    thread_name_prefix="instrument-parse",
                )
    return _pool


def max_upload_bytes() -> int:
    return int(config.INSTRUMENT_MAX_UPLOAD_MB) * 1024 * 1024


async def _stream_to_disk(file: UploadFile, head: bytes, path: str) -> int:
    """Write ``head`` + the rest of the upload to ``path``; returns byte count.
    Enforces the instrument size ceiling and removes the partial file on
    overflow (413)."""
    limit = max_upload_bytes()
    written = 0
    with open(path, "wb") as fh:
        fh.write(head)
        written += len(head)
        while True:
            chunk = await file.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                fh.close()
                dataset_files.remove_files(path)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"Instrument file too large. Max {config.INSTRUMENT_MAX_UPLOAD_MB} MB "
                        f"(INSTRUMENT_MAX_UPLOAD_MB)."
                    ),
                )
            fh.write(chunk)
    return written


async def ingest_upload(
    file: UploadFile,
    head: bytes,
    parser_id: str,
    user_id: str,
    background_tasks: BackgroundTasks,
    parent_file_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Take the sniffed upload down the parser path. Returns the compact
    dataset record the frontend tracks (``kind: "dataset"``, status queued)."""
    parser = registry.get(parser_id)
    if parser is None:  # cannot happen (sniff returned it) but never 500 on it
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown parser")
    filename = file.filename or f"upload{parser.extensions[0] if parser.extensions else ''}"

    # Reserve an id first so the raw file is addressed by it, then stream.
    from bson import ObjectId  # local: keeps this module import-light for tests

    provisional_id = str(ObjectId())
    raw_path = dataset_files.raw_path_for(provisional_id, filename)
    size = await _stream_to_disk(file, head, raw_path)

    created = await dataset_store.create_dataset(
        user_id,
        filename=filename,
        size_bytes=size,
        parser_id=parser.id,
        dataset_kind=parser.dataset_kind,
        label=parser.label,
        raw_path=raw_path,
        parent_file_id=parent_file_id,
    )
    dataset_id, job_id = created["dataset_id"], created["job_id"]
    # The raw file was named by the provisional id; rename to the real id so
    # everything on disk is addressed by the Mongo dataset id.
    final_raw = dataset_files.raw_path_for(dataset_id, filename)
    try:
        os.replace(raw_path, final_raw)
        raw_path = final_raw
        await dataset_store._set_both(user_id, dataset_id, job_id, {}, {"raw_path": raw_path})
    except OSError:
        pass  # keep the provisional path; the pointer already records it

    background_tasks.add_task(
        run_parse_job, user_id, dataset_id, job_id, raw_path, parser.id, filename
    )
    return {
        "id": dataset_id,
        "kind": "dataset",
        "filename": filename,
        "extension": dataset_files.safe_extension(filename),
        "status": dataset_store.STATE_QUEUED,
        "progress": 0,
        "dataset_id": dataset_id,
        "job_id": job_id,
        "parser_id": parser.id,
        "dataset_kind": parser.dataset_kind,
        "label": parser.label,
        "badge": parser.label,
        "size_bytes": size,
    }


def _user_facing_error(exc: BaseException) -> str:
    if isinstance(exc, ParserError):
        return f"Could not parse this file: {exc}"
    if isinstance(exc, MemoryError):
        return "The file is too large to parse on this server (out of memory)."
    return f"Parsing failed: {type(exc).__name__}: {exc}"


async def run_parse_job(
    user_id: str,
    dataset_id: str,
    job_id: str,
    raw_path: str,
    parser_id: str,
    filename: Optional[str] = None,
) -> None:
    """Background job: parse ``raw_path`` with ``parser_id`` and finalise the
    dataset. Never raises -- every failure lands in the job/dataset docs.
    ``filename`` is the ORIGINAL upload name (the raw file on disk is named by
    the dataset id) and is stamped into ``metadata["source_filename"]``."""
    parser = registry.get(parser_id)
    loop = asyncio.get_running_loop()
    if parser is None:
        await dataset_store.mark_failed(user_id, dataset_id, job_id, f"Unknown parser {parser_id!r}")
        return
    await dataset_store.mark_parsing(user_id, dataset_id, job_id)

    last = {"t": 0.0, "pct": -1}

    def _progress(frac: float) -> None:
        # Called from the parser thread. Throttle, then hand the write to the
        # loop without waiting on it (fire-and-forget; the parse must not stall
        # on Mongo latency).
        pct = int(frac * 100)
        now = time.monotonic()
        if pct <= last["pct"] or (now - last["t"] < _PROGRESS_MIN_INTERVAL_S and pct < 100):
            return
        last["t"], last["pct"] = now, pct
        try:
            asyncio.run_coroutine_threadsafe(
                dataset_store.update_progress(user_id, dataset_id, job_id, pct), loop
            )
        except RuntimeError:
            pass  # loop closing; the final state write below still happens

    t0 = time.perf_counter()
    try:
        result = await loop.run_in_executor(_get_pool(), parser.parse, raw_path, _progress)
        if filename:
            result.metadata["source_filename"] = filename
        npz_path = await loop.run_in_executor(
            _get_pool(), dataset_files.save_arrays, dataset_id, result.arrays
        )
        elapsed = round(time.perf_counter() - t0, 3)
        await dataset_store.mark_parsed(
            user_id, dataset_id, job_id,
            metadata=result.metadata,
            shapes=result.shapes(),
            dtypes=result.dtypes(),
            warnings=result.warnings,
            npz_path=npz_path,
            elapsed_s=elapsed,
        )
        logger.info(
            "instrument parse ok: dataset=%s parser=%s shapes=%s warnings=%d in %.1fs",
            dataset_id, parser_id, result.shapes(), len(result.warnings), elapsed,
        )
    except Exception as exc:  # noqa: BLE001 - must land in the job doc, never propagate
        logger.exception("instrument parse failed: dataset=%s parser=%s", dataset_id, parser_id)
        try:
            await dataset_store.mark_failed(user_id, dataset_id, job_id, _user_facing_error(exc))
        except Exception:  # noqa: BLE001
            logger.exception("could not record parse failure for dataset=%s", dataset_id)


async def retry_dataset(user_id: str, dataset_id: str, background_tasks: BackgroundTasks) -> Optional[Dict[str, Any]]:
    """Re-queue a dataset's parse from its retained raw file. None if the
    dataset is not this user's; raises 409 if the raw file is gone."""
    doc = await dataset_store.get_dataset_doc(user_id, dataset_id)
    if doc is None:
        return None
    raw_path = doc.get("raw_path")
    if not raw_path or not os.path.exists(raw_path):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The original upload is no longer on disk; upload the file again.",
        )
    job_id = await dataset_store.requeue(user_id, dataset_id)
    background_tasks.add_task(
        run_parse_job, user_id, dataset_id, job_id, raw_path, doc["parser_id"], doc.get("filename")
    )
    return await dataset_store.get_dataset(user_id, dataset_id)
