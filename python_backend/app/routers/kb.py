"""Student KB upload endpoint (Phase 4).

POST /api/kb/upload — any authenticated user may contribute to the shared
knowledge base; no approval gate. Everything carries provenance so a bad upload
is cheap to remove (Phase 6).

Two-phase, single endpoint:
  * FIRST call (file only): extract + validate + LLM metadata prefill, then return
    ``needs_input`` with the prefilled form, any soft warnings, and missing
    required fields. Nothing is indexed.
  * SECOND call (file + corrected metadata + acknowledged warnings): re-validate,
    then ingest with provenance stamped on every chunk and return the batch id.

HARD failures (format, size, pages, extraction quality, EXACT-hash duplicate)
reject outright. SOFT flags (near-duplicate, off-topic, PII, non-English) are
returned for the student to acknowledge, never blocking.

Gated behind ``KB_UPLOAD_ENABLED`` (off by default) -> 404 when disabled.

Versioning: a re-upload of the same project+canonical-title supersedes the prior
version — confirm-gated (the student acknowledges a "replaces X (N chunks)"
warning), then delete-before-insert so retrieval never mixes v1 and v2.
"""
import asyncio
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.core import config
from app.core.database import files_collection, kb_audit_collection
from app.core.rate_limit import limiter, rate_limit_identify, user_id_key
from app.dependencies.auth import get_current_user
from app.services import kb_validation as val
from app.services.kb_formats import ACCEPTED_LABEL, UnsupportedFormatError, extract_kb_document, get_handler
from app.services.kb_metadata import extract_metadata
from app.services.kb_provenance import build_provenance
from app.services.rag_service import get_embedding_model, ingest_document, ingest_try_acquire, ingest_release
from models import User

router = APIRouter(prefix="/api/kb", tags=["kb-upload"])

KB_CATEGORY = "knowledge_base"
# First-chunk proxy length for dedup/relevance (approx one v2 chunk).
_FIRST_CHUNK_CHARS = 1500


def _require_enabled() -> None:
    if not config.KB_UPLOAD_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


ADMIN_ROLES = frozenset({"admin", "professor"})


def _is_admin(user) -> bool:
    return getattr(user, "role", "user") in ADMIN_ROLES


async def _audit(action: str, actor_id, actor_name, source: str, **details) -> None:
    """Best-effort audit record (never blocks the operation it describes)."""
    try:
        await kb_audit_collection.insert_one({
            "action": action,
            "actorId": str(actor_id) if actor_id else None,
            "actorName": actor_name,
            "source": source,
            "at": datetime.now(),
            **details,
        })
    except Exception as e:
        print(f"[KB_AUDIT] failed to record {action}: {e}")


def _all_text(pages: List[Tuple[int, str, bool]]) -> str:
    return "\n".join(t for _, t, _ in pages)


def _embed_first_chunk(text: str) -> List[float]:
    model = get_embedding_model()
    v = list(model.embed([text[:_FIRST_CHUNK_CHARS]]))[0]
    return v.tolist() if hasattr(v, "tolist") else list(v)


async def _supersede_plan(project_tag: str, canonical_title: str) -> Dict[str, Any]:
    """DRY-RUN: what a re-upload of the same (project, canonical title) would
    supersede. Returns the filter, the current max version, and how many existing
    chunks match — WITHOUT deleting anything (Phase 4 gate)."""
    supersede_filter = {
        "category": KB_CATEGORY,
        "projectTag": project_tag,
        "canonicalTitle": canonical_title,
    }
    count = await files_collection.count_documents(supersede_filter)
    prior_versions = await files_collection.distinct("version", supersede_filter)
    max_version = max([v for v in prior_versions if isinstance(v, int)], default=0)
    return {
        "filter": supersede_filter,
        "would_delete_chunks": count,
        "prior_versions": sorted(v for v in prior_versions if isinstance(v, int)),
        "next_version": max_version + 1,
    }


# ---------------------------------------------------------------------------
# Shared per-file pipeline — used by BOTH the single-upload task and the bulk
# task. Do NOT fork these: validation/dedup live in _prepare_kb_file, and the
# ingest path (supersede + provenance + ingest_document + audit) in
# _ingest_prepared_file.
# ---------------------------------------------------------------------------
async def _prepare_kb_file(content: bytes, filename: str) -> Dict[str, Any]:
    """Deterministic per-file validation + dedup. Returns extraction output plus
    every validation signal. A non-None ``skip_reason`` is a HARD reject:
    unsupported / corrupt / too_many_pages / scanned / duplicate."""
    out: Dict[str, Any] = {"filename": filename, "skip_reason": None}
    try:
        result = extract_kb_document(content, filename)
    except UnsupportedFormatError:
        out["skip_reason"] = "unsupported"
        return out
    except Exception as e:
        print(f"[KB_UPLOAD] extraction failed for {filename}: {e}")
        out["skip_reason"] = "corrupt"
        return out

    pages = result.pages
    if not val.check_pages(len(pages))[0]:
        out["skip_reason"] = "too_many_pages"
        return out
    h = get_handler(filename)
    handler_ok = h.validate(result)[0] if h is not None else True
    if not val.extraction_quality(pages)[0] or handler_ok is False:
        out["skip_reason"] = "scanned"
        return out

    content_hash = val.normalized_text_hash(pages)
    all_text = _all_text(pages)
    first_emb = _embed_first_chunk(all_text)
    dedup = await val.check_duplicate(content_hash, first_emb)
    if dedup.exact_match is not None:
        out["skip_reason"] = "duplicate"
        out["duplicate_of"] = dedup.exact_match.get("canonicalTitle") or dedup.exact_match.get("filename")
        out["content_hash"] = content_hash
        return out

    centroid = await val.get_kb_centroid()
    relevance_cos, relevance_outlier = (None, False)
    if centroid:
        relevance_cos, relevance_outlier = val.relevance(first_emb, centroid)
    pii = val.scan_pii(all_text)
    pii_sensitive = val.sensitive_pii(pii)  # gating subset: student numbers + phones
    non_english, lang_note = val.detect_non_english(all_text)

    out.update({
        "pages": pages, "source_format": result.source_format,
        "format_metadata": result.format_metadata, "content_hash": content_hash,
        "all_text": all_text, "first_emb": first_emb,
        "near_dup": dedup.near_match, "near_cosine": dedup.near_cosine,
        "relevance_cos": relevance_cos, "relevance_outlier": relevance_outlier,
        "pii": pii, "pii_sensitive": pii_sensitive, "non_english": non_english, "lang_note": lang_note,
    })
    return out


async def _ingest_prepared_file(
    prep: Dict[str, Any], *, canonical_title: str, project: str, doc_type: str,
    year, authors, publication, batch_id: str, uploader_id: str, uploader_name: str,
) -> Dict[str, Any]:
    """The shared INGEST PATH: supersede (delete-before-insert) + provenance +
    ingest_document + audit. Used by the single AND bulk background tasks. The
    caller owns the ingest-queue slot and the content-hash reservation, and
    updates its own status record."""
    filename = prep["filename"]
    plan = await _supersede_plan(project, canonical_title)
    version = plan["next_version"]

    author_list = ([a.strip() for a in authors.split(";") if a.strip()]
                   if isinstance(authors, str) else [str(a).strip() for a in (authors or []) if str(a).strip()])
    try:
        year_int = int(str(year).strip())
    except (ValueError, TypeError):
        year_int = None
    prov = build_provenance(
        uploader_id=uploader_id, uploader_name=uploader_name, uploaded_at=datetime.now(),
        project_tag=project.strip(), doc_type=doc_type.strip(),
        source_format=prep["source_format"], batch_id=batch_id,
        canonical_title=canonical_title, version=version, permission_confirmed=True,
    )
    prov["contentHash"] = prep["content_hash"]
    prov["authors"] = author_list
    prov["year"] = year_int
    if isinstance(publication, str) and publication.strip():
        prov["publication"] = publication.strip()

    if plan["would_delete_chunks"] > 0:
        print(f"[KB_SUPERSEDE] filter={plan['filter']} deleting {plan['would_delete_chunks']} "
              f"prior-version chunk(s) for '{canonical_title}' before inserting v{version}")
        del_res = await files_collection.delete_many(plan["filter"])
        print(f"[KB_SUPERSEDE] deleted {del_res.deleted_count} chunk(s)")

    # pre_extracted_pages is authoritative here, so ingest_document ignores the
    # (empty) content arg and never re-extracts.
    res = await ingest_document(filename, b"", category=KB_CATEGORY, user_id=uploader_id,
                                pre_extracted_pages=prep["pages"], provenance=prov)
    await _audit("upload", uploader_id, uploader_name, "endpoint",
                 batchId=batch_id, canonicalTitle=canonical_title,
                 project=project, version=version, chunks=res.get("chunks_created", 0))
    return {"chunkCount": res.get("chunks_created", 0), "canonicalTitle": canonical_title,
            "version": version, "superseded": plan["would_delete_chunks"]}


@router.post("/upload")
@limiter.limit(config.RATE_LIMIT_UPLOAD, key_func=user_id_key)
@limiter.limit(config.RATE_LIMIT_UPLOAD_HOURLY, key_func=user_id_key)
async def kb_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project: str = Form(""),
    docType: str = Form(""),
    year: str = Form(""),
    permissionConfirmed: bool = Form(False),
    title: str = Form(""),
    authors: str = Form(""),
    publication: str = Form(""),
    acknowledge: str = Form(""),  # comma-separated warning kinds the student accepted
    current_user: User = Depends(rate_limit_identify),
):
    _require_enabled()

    # --- read + hard format/size/extraction gates ----------------------------
    filename = val.sanitize_filename(file.filename or "upload")
    content = await file.read()

    ok, reason = val.check_size(len(content))
    if not ok:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=reason)

    prep = await _prepare_kb_file(content, filename)
    skip = prep.get("skip_reason")
    if skip == "unsupported":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unsupported file type. Accepted formats: {ACCEPTED_LABEL}.")
    if skip == "corrupt":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Could not read this {ACCEPTED_LABEL} file. It may be corrupt.")
    if skip == "too_many_pages":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Too many pages (max {config.KB_MAX_PAGES}).")
    if skip == "scanned":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="This looks like a scanned or image-only document — no usable text could be extracted.")
    if skip == "duplicate":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"This document is already in the knowledge base as \"{prep['duplicate_of']}\".")

    content_hash = prep["content_hash"]
    if not val.reserve_hash(content_hash):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="An identical file is being uploaded right now. Please wait a moment.")

    handed_off = False  # True once the background ingest task owns the reservation
    try:
        # Soft warnings the student may acknowledge (unchanged policy).
        warnings: List[Dict[str, Any]] = []
        if prep["near_dup"] is not None:
            nm = prep["near_dup"]
            warnings.append({
                "kind": "duplicate",
                "message": (f"Looks very similar to an existing document "
                            f"(\"{nm.get('canonicalTitle') or nm.get('filename')}\", "
                            f"{prep['near_cosine']:.0%} match). Upload anyway?"),
                "match": nm.get("canonicalTitle") or nm.get("filename"),
            })
        if prep["relevance_outlier"]:
            warnings.append({
                "kind": "relevance",
                "message": (f"This doesn't look closely related to the geotechnical knowledge base "
                            f"(relevance {prep['relevance_cos']:.2f}). Add it anyway?"),
            })
        if prep["pii_sensitive"]:
            warnings.append({"kind": "pii",
                             "message": f"Possible sensitive personal data detected ({', '.join(prep['pii_sensitive'].keys())}). Confirm it's okay to index.",
                             "details": prep["pii_sensitive"]})
        if prep["non_english"]:
            warnings.append({"kind": "language",
                             "message": f"This may not be in English ({prep['lang_note']}). Add it anyway?"})

        acknowledged = {k.strip() for k in acknowledge.split(",") if k.strip()}
        unconfirmed = [w for w in warnings if w["kind"] not in acknowledged]

        missing = [f for f, v in (("project", project), ("docType", docType), ("year", year)) if not str(v).strip()]
        if not permissionConfirmed:
            missing.append("permissionConfirmed")

        extraction_info = {"source_format": prep["source_format"], "pages": len(prep["pages"]),
                           "format_metadata": prep["format_metadata"]}
        if missing or unconfirmed:
            return {
                "status": "needs_input",
                "missing_fields": missing,
                "warnings": unconfirmed,
                "prefilled_metadata": await extract_metadata(prep["all_text"], filename),
                "extraction": extraction_info,
            }

        canonical_title = (title or "").strip()
        if not canonical_title:
            canonical_title = (await extract_metadata(prep["all_text"], filename))["title"]

        plan = await _supersede_plan(project, canonical_title)
        if plan["would_delete_chunks"] > 0 and "supersede" not in acknowledged:
            return {
                "status": "needs_input",
                "missing_fields": [],
                "warnings": [{
                    "kind": "supersede",
                    "message": (f"This replaces the existing \"{canonical_title}\" in project "
                                f"\"{project}\" (version {max(plan['prior_versions'], default=1)}, "
                                f"{plan['would_delete_chunks']} chunks). The old version will be "
                                f"removed and replaced. Continue?"),
                    "would_delete_chunks": plan["would_delete_chunks"],
                }],
                "prefilled_metadata": await extract_metadata(prep["all_text"], filename),
                "extraction": extraction_info,
            }

        if not ingest_try_acquire():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="The server is busy indexing uploads. Please try again shortly.")

        batch_id = uuid.uuid4().hex
        uploader_name = getattr(current_user, "full_name", None) or getattr(current_user, "email", "student")
        await files_collection.insert_one({
            "docType": "kb_batch", "batchId": batch_id, "uploaderId": str(current_user.id),
            "uploaderName": uploader_name, "status": "indexing", "filename": filename,
            "canonicalTitle": canonical_title, "projectTag": project.strip(),
            "version": plan["next_version"], "sourceFormat": prep["source_format"],
            "supersede": {"would_delete_chunks": plan["would_delete_chunks"],
                          "prior_versions": plan["prior_versions"]},
            "createdAt": datetime.now(),
        })
        background_tasks.add_task(
            _kb_single_ingest_task, prep, canonical_title, project, docType, year,
            authors, publication, batch_id, str(current_user.id), uploader_name, content_hash,
        )
        handed_off = True
        return {
            "status": "indexing", "batchId": batch_id, "canonicalTitle": canonical_title,
            "version": plan["next_version"],
            "supersede": {"would_delete_chunks": plan["would_delete_chunks"]},
        }
    finally:
        if not handed_off:
            val.release_hash(content_hash)


async def _kb_single_ingest_task(
    prep, canonical_title, project, doc_type, year, authors, publication,
    batch_id, uploader_id, uploader_name, content_hash,
):
    """Background ingest for a SINGLE upload; delegates to the shared ingest path
    (_ingest_prepared_file) and updates the kb_batch status doc. Releases the
    queue slot + hash reservation when done."""
    try:
        outcome = await _ingest_prepared_file(
            prep, canonical_title=canonical_title, project=project, doc_type=doc_type,
            year=year, authors=authors, publication=publication, batch_id=batch_id,
            uploader_id=uploader_id, uploader_name=uploader_name,
        )
        sample = await files_collection.find_one(
            {"category": KB_CATEGORY, "batchId": batch_id, "chunkIndex": 0}, {"text": 1})
        await files_collection.update_one(
            {"docType": "kb_batch", "batchId": batch_id},
            {"$set": {"status": "indexed", "chunkCount": outcome["chunkCount"],
                      "sampleChunk": ((sample or {}).get("text") or "")[:400],
                      "processedAt": datetime.now()}},
        )
        await val.get_kb_centroid(force=True)
        print(f"[KB_UPLOAD] batch {batch_id} indexed ({outcome['chunkCount']} chunks)")
    except Exception as e:
        print(f"[KB_UPLOAD] background ingest failed for batch {batch_id}: {e}")
        import traceback
        traceback.print_exc()
        await files_collection.update_one(
            {"docType": "kb_batch", "batchId": batch_id},
            {"$set": {"status": "failed", "error": str(e), "processedAt": datetime.now()}},
        )
    finally:
        ingest_release()
        val.release_hash(content_hash)


# ---------------------------------------------------------------------------
# Bulk (multi-file) upload — one batchId, shared metadata, per-file LLM titles.
# Returns immediately; a sequential, PACED background worker reuses the SAME
# per-file pipeline (_prepare_kb_file + _ingest_prepared_file) so nothing forks.
# ---------------------------------------------------------------------------
async def _set_bulk_file(batch_id: str, idx: int, status_: str, *, reason=None, extra=None):
    upd: Dict[str, Any] = {f"files.{idx}.status": status_}
    if reason:
        upd[f"files.{idx}.reason"] = reason
    for k, v in (extra or {}).items():
        upd[f"files.{idx}.{k}"] = v
    await files_collection.update_one({"docType": "kb_batch", "batchId": batch_id}, {"$set": upd})


async def _acquire_ingest_slot(max_wait: float = 120.0) -> bool:
    """Wait (1s backoff) for a queue-depth slot so a bulk batch never exceeds the
    cap or starves chat/Ollama. Returns True if a slot was reserved; after
    max_wait, proceeds anyway (returns False) so the batch can't stall forever."""
    waited = 0.0
    while waited < max_wait:
        if ingest_try_acquire():
            return True
        await asyncio.sleep(1.0)
        waited += 1.0
    return False


async def _kb_bulk_task(tmp_dir, file_entries, project, doc_type, year,
                        uploader_id, uploader_name, batch_id, acknowledged=frozenset()):
    """Process a bulk batch SEQUENTIALLY with pacing. Each file goes through the
    shared _prepare_kb_file + _ingest_prepared_file path; the per-file outcome is
    written to the kb_batch doc's files[] so the panel shows live progress. The
    student can close the tab — this keeps running server-side."""
    done = 0
    try:
        for dest, name, idx in file_entries:
            await _set_bulk_file(batch_id, idx, "processing")
            acquired = await _acquire_ingest_slot()
            reserved_hash = None
            try:
                with open(dest, "rb") as fh:
                    content = fh.read()
                prep = await _prepare_kb_file(content, name)
                sr = prep.get("skip_reason")
                if sr:
                    await _set_bulk_file(batch_id, idx, "skipped", reason=sr,
                                         extra=({"duplicateOf": prep.get("duplicate_of")} if sr == "duplicate" else None))
                elif prep["pii_sensitive"] and "pii" not in acknowledged:
                    # Sensitive PII (student number / phone) and not batch-acknowledged.
                    # Emails are public author contact and never reach here.
                    await _set_bulk_file(batch_id, idx, "skipped", reason="pii")
                elif not val.reserve_hash(prep["content_hash"]):
                    await _set_bulk_file(batch_id, idx, "skipped", reason="duplicate")
                else:
                    reserved_hash = prep["content_hash"]
                    meta = await extract_metadata(prep["all_text"], name)
                    per_year = meta.get("year") or year  # per-file year, else the shared batch year
                    outcome = await _ingest_prepared_file(
                        prep, canonical_title=meta["title"], project=project, doc_type=doc_type,
                        year=per_year, authors=meta.get("authors", []), publication="",
                        batch_id=batch_id, uploader_id=uploader_id, uploader_name=uploader_name)
                    await _set_bulk_file(batch_id, idx, "done", extra={
                        "chunkCount": outcome["chunkCount"], "canonicalTitle": outcome["canonicalTitle"],
                        "version": outcome["version"]})
            except Exception as e:
                print(f"[KB_BULK] file '{name}' failed: {e}")
                import traceback
                traceback.print_exc()
                await _set_bulk_file(batch_id, idx, "failed", extra={"error": str(e)[:200]})
            finally:
                if acquired:
                    ingest_release()
                if reserved_hash:
                    val.release_hash(reserved_hash)
                try:
                    os.remove(dest)
                except OSError:
                    pass
            done += 1
            await files_collection.update_one({"docType": "kb_batch", "batchId": batch_id},
                                              {"$set": {"done": done}})
            await asyncio.sleep(config.KB_BULK_PACING_SECONDS)  # pace: never monopolise the pool

        await val.get_kb_centroid(force=True)  # KB changed -> refresh the cached centroid once
        await files_collection.update_one({"docType": "kb_batch", "batchId": batch_id},
                                          {"$set": {"status": "done", "processedAt": datetime.now()}})
        await _audit("bulk_upload", uploader_id, uploader_name, "endpoint",
                     batchId=batch_id, project=project, total=len(file_entries), done=done)
        print(f"[KB_BULK] batch {batch_id} complete ({done} files)")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/bulk-upload")
@limiter.limit(config.RATE_LIMIT_UPLOAD, key_func=user_id_key)
@limiter.limit(config.RATE_LIMIT_UPLOAD_HOURLY, key_func=user_id_key)
async def kb_bulk_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    project: str = Form(""),
    docType: str = Form(""),
    year: str = Form(""),
    permissionConfirmed: bool = Form(False),
    acknowledge: str = Form(""),  # batch-level acks, e.g. "pii" to add sensitive-PII files
    current_user: User = Depends(rate_limit_identify),
):
    """Bulk multi-file KB upload. Shared metadata (project/docType/year/permission)
    once; per-file title/authors/year come from LLM extraction. Returns a single
    batchId immediately; ingestion runs in the paced background worker. The batch
    is bulk-removable via kb_remove --batch <batchId>."""
    _require_enabled()

    missing = [f for f, v in (("project", project), ("docType", docType), ("year", year)) if not str(v).strip()]
    if not permissionConfirmed:
        missing.append("permissionConfirmed")
    if missing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Missing required batch metadata: {', '.join(missing)}")
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided.")
    if len(files) > config.KB_BULK_MAX_FILES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Too many files ({len(files)}). Max {config.KB_BULK_MAX_FILES} per batch.")

    batch_id = uuid.uuid4().hex
    tmp_dir = tempfile.mkdtemp(prefix=f"kbbulk_{batch_id}_")
    file_status: List[Dict[str, Any]] = []
    file_entries: List[Tuple[str, str, int]] = []
    for i, f in enumerate(files):
        name = val.sanitize_filename(f.filename or f"file_{i}")
        idx = len(file_status)
        dest = os.path.join(tmp_dir, f"{i:03d}")
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)  # stream to disk — memory-safe for big batches
        ok, _reason = val.check_size(os.path.getsize(dest))
        if not ok:
            file_status.append({"filename": name, "status": "skipped", "reason": "too_large"})
            os.remove(dest)
            continue
        file_status.append({"filename": name, "status": "queued"})
        file_entries.append((dest, name, idx))

    uploader_name = getattr(current_user, "full_name", None) or getattr(current_user, "email", "student")
    await files_collection.insert_one({
        "docType": "kb_batch", "kind": "bulk", "batchId": batch_id,
        "uploaderId": str(current_user.id), "uploaderName": uploader_name,
        "status": "processing", "projectTag": project.strip(),
        "total": len(files), "done": 0, "files": file_status,
        "createdAt": datetime.now(),
    })
    acknowledged = {k.strip() for k in acknowledge.split(",") if k.strip()}
    background_tasks.add_task(
        _kb_bulk_task, tmp_dir, file_entries, project, docType, year,
        str(current_user.id), uploader_name, batch_id, acknowledged,
    )
    return {"status": "queued", "batchId": batch_id, "total": len(files),
            "accepted": len(file_entries)}


@router.get("/status")
async def kb_status():
    """Ungated: lets the frontend decide whether to show the KB nav + panel."""
    return {"enabled": config.KB_UPLOAD_ENABLED}


@router.get("/batch/{batch_id}")
async def kb_batch_status(batch_id: str, current_user: User = Depends(get_current_user)):
    """Poll one upload's ingest progress. Scoped to the uploader."""
    _require_enabled()
    doc = await files_collection.find_one(
        {"docType": "kb_batch", "batchId": batch_id},
        {"status": 1, "chunkCount": 1, "canonicalTitle": 1, "version": 1,
         "sampleChunk": 1, "error": 1, "uploaderId": 1, "projectTag": 1,
         "filename": 1, "sourceFormat": 1, "supersede": 1,
         "kind": 1, "total": 1, "done": 1, "files": 1},
    )
    if not doc or str(doc.get("uploaderId")) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return {k: doc.get(k) for k in (
        "status", "chunkCount", "canonicalTitle", "version", "sampleChunk",
        "error", "projectTag", "filename", "sourceFormat", "supersede",
        "kind", "total", "done", "files")}


@router.get("/my-uploads")
async def kb_my_uploads(current_user: User = Depends(get_current_user)):
    """The caller's own recent KB uploads, newest first, with a `deletable` flag
    (within the self-delete window). Drives the panel's 'your uploads' list."""
    _require_enabled()
    cur = files_collection.find(
        {"docType": "kb_batch", "uploaderId": str(current_user.id)},
        {"batchId": 1, "status": 1, "canonicalTitle": 1, "version": 1, "projectTag": 1,
         "filename": 1, "chunkCount": 1, "sourceFormat": 1, "createdAt": 1},
    ).sort("createdAt", -1).limit(50)
    items = []
    async for d in cur:
        created = d.get("createdAt")
        age_h = ((datetime.now() - created).total_seconds() / 3600) if created else 1e9
        items.append({
            "batchId": d.get("batchId"),
            "status": d.get("status"),
            "canonicalTitle": d.get("canonicalTitle"),
            "version": d.get("version"),
            "projectTag": d.get("projectTag"),
            "filename": d.get("filename"),
            "chunkCount": d.get("chunkCount"),
            "sourceFormat": d.get("sourceFormat"),
            "createdAt": created.isoformat() if created else None,
            "deletable": age_h <= config.KB_SELF_DELETE_WINDOW_HOURS,
        })
    return {"uploads": items}


@router.get("/uploads")
async def kb_admin_uploads(
    uploader: Optional[str] = None,
    project: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Admin/professor view of recent uploads (uploader, project, date, chunks).
    Optional ?uploader / ?project filters."""
    _require_enabled()
    if not _is_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    q: Dict[str, Any] = {"docType": "kb_batch"}
    if uploader:
        q["uploaderId"] = uploader
    if project:
        q["projectTag"] = project
    items: List[Dict[str, Any]] = []
    async for d in files_collection.find(q).sort("createdAt", -1).limit(200):
        created = d.get("createdAt")
        items.append({
            "batchId": d.get("batchId"),
            "uploaderId": d.get("uploaderId"),
            "uploaderName": d.get("uploaderName"),
            "project": d.get("projectTag"),
            "canonicalTitle": d.get("canonicalTitle"),
            "version": d.get("version"),
            "chunkCount": d.get("chunkCount"),
            "status": d.get("status"),
            "sourceFormat": d.get("sourceFormat"),
            "createdAt": created.isoformat() if created else None,
        })
    return {"uploads": items}


@router.delete("/batch/{batch_id}")
async def kb_delete_batch(batch_id: str, current_user: User = Depends(get_current_user)):
    """Delete one upload. Admins/professors may delete ANY batch; a student may
    delete their OWN upload within the self-delete window. Audited either way."""
    _require_enabled()
    b = await files_collection.find_one(
        {"docType": "kb_batch", "batchId": batch_id},
        {"uploaderId": 1, "createdAt": 1, "canonicalTitle": 1, "projectTag": 1},
    )
    admin = _is_admin(current_user)
    own = bool(b) and str(b.get("uploaderId")) == str(current_user.id)
    # 404 (not 403) when neither owner nor admin, so existence isn't revealed.
    if not b or (not admin and not own):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")

    if not admin:
        created = b.get("createdAt")
        if created is not None:
            age_hours = (datetime.now() - created).total_seconds() / 3600
            if age_hours > config.KB_SELF_DELETE_WINDOW_HOURS:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(f"This upload is older than the {config.KB_SELF_DELETE_WINDOW_HOURS}h "
                            f"self-delete window. Ask an admin to remove it."),
                )

    chunk_filter: Dict[str, Any] = {"category": KB_CATEGORY, "batchId": batch_id}
    if not admin:  # owner path stays scoped to their own chunks (defense in depth)
        chunk_filter["uploaderId"] = str(current_user.id)
    chunk_res = await files_collection.delete_many(chunk_filter)
    await files_collection.delete_one({"docType": "kb_batch", "batchId": batch_id})
    await val.get_kb_centroid(force=True)
    await _audit("delete", str(current_user.id),
                 getattr(current_user, "full_name", None) or getattr(current_user, "email", None),
                 "endpoint", batchId=batch_id, canonicalTitle=b.get("canonicalTitle"),
                 project=b.get("projectTag"), chunks=chunk_res.deleted_count, admin=admin)
    print(f"[KB_UPLOAD] delete batch {batch_id}: {chunk_res.deleted_count} chunks by "
          f"{current_user.id} (admin={admin})")
    return {"success": True, "deleted_chunks": chunk_res.deleted_count}
