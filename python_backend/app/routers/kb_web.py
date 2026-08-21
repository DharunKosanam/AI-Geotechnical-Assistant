"""KB web-page ingestion by pasted URL (WEB_INGEST_ENABLED).

A fetched page becomes an ORDINARY KB document — same chunker, embeddings,
retrieval and reranking — with web provenance on every chunk:
``sourceFormat: "web"``, ``canonicalUrl`` (the URL after redirects) and
``fetchedAt``. No crawler, no live fetch at query time.

Endpoints (all require an authenticated user — the same population that may
use /api/kb/upload; restrict here if Dharun decides admins-only):

* ``POST /preview`` — fetch + extract, return the title and first lines so the
  user sees what will be stored BEFORE confirming. Nothing is indexed.
* ``POST /ingest`` — fetch again, validate, then index. A URL already in the
  KB is rejected (409, ``already_ingested``) unless ``refresh`` is set, in
  which case the existing chunks are superseded via the same
  delete-before-insert flow the KB file path uses, keyed on ``canonicalUrl``
  (a page's title may change between fetches; its URL is the stable key).
  The response carries the previous and new ``fetchedAt``.
* ``GET /status`` — flag state + allowlist, for debugging and the UI.

Flag-gated at REGISTRATION (highlights pattern): ``register(app)`` includes
the router only when config.WEB_INGEST_ENABLED is on, so with the flag off
the routes are absent (not present-and-404ing) — plus a call-time gate for
defense in depth. Fetch failures surface as structured
``{"code", "message"}`` details so the UI can render each case specifically.
"""
import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from app.core import config
from app.core.database import files_collection
from app.core.rate_limit import limiter, rate_limit_identify, user_id_key
from app.routers.kb import KB_CATEGORY, _audit
from app.services import kb_validation as val
from app.services.kb_formats import build_web_document, validate_web_document
from app.services.kb_provenance import build_provenance
from app.services.rag_service import ingest_document, ingest_release, ingest_try_acquire
from app.services.web_extract import extract_web_page
from app.services.web_fetch import fetch_web_page
from models import User

router = APIRouter(prefix="/api/kb/web", tags=["kb-web-ingest"])

PREVIEW_CHARS = 1500

# Fetcher error code -> HTTP status. The code travels in detail["code"] so the
# frontend renders a SPECIFIC message per case, never a generic failure.
_FETCH_HTTP_STATUS = {
    "not_allowlisted": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "private_address": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "bad_scheme": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "bad_port": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "invalid_url": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "dns_failure": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "too_many_redirects": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "login_wall": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "wrong_content_type": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "http_error": status.HTTP_502_BAD_GATEWAY,
    "fetch_error": status.HTTP_502_BAD_GATEWAY,
}


def _require_enabled() -> None:
    if not config.WEB_INGEST_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


class WebPreviewRequest(BaseModel):
    url: str


class WebIngestRequest(BaseModel):
    url: str
    project: str = ""
    title: str = ""            # optional override of the page <title>
    refresh: bool = False      # supersede an already-ingested URL
    permissionConfirmed: bool = False


def _fail(status_code: int, code: str, message: str, **extra) -> None:
    raise HTTPException(status_code=status_code,
                        detail={"code": code, "message": message, **extra})


def _iso(dt: Any) -> Optional[str]:
    return dt.isoformat() if hasattr(dt, "isoformat") else dt


async def _fetch_and_extract(url: str):
    """Guarded fetch (in a worker thread — sync httpx) + extraction; raises a
    structured HTTPException on any fetch failure."""
    fetched = await asyncio.to_thread(fetch_web_page, url)
    if not fetched.ok:
        _fail(_FETCH_HTTP_STATUS.get(fetched.error, status.HTTP_422_UNPROCESSABLE_ENTITY),
              fetched.error, fetched.message, resolvedUrl=fetched.url or None)
    extracted = extract_web_page(fetched.html, fetched.url)
    return fetched, extracted


async def _existing_web_doc(canonical_url: str) -> Optional[Dict[str, Any]]:
    """First chunk of an already-ingested page for this exact resolved URL."""
    return await files_collection.find_one(
        {"category": KB_CATEGORY, "canonicalUrl": canonical_url, "chunkIndex": 0},
        {"canonicalTitle": 1, "fetchedAt": 1, "version": 1, "batchId": 1, "contentHash": 1},
    )


async def _web_supersede_plan(canonical_url: str) -> Dict[str, Any]:
    """DRY-RUN of what a refresh would replace, keyed on canonicalUrl (the
    web analogue of kb._supersede_plan — same delete-before-insert flow)."""
    supersede_filter = {"category": KB_CATEGORY, "canonicalUrl": canonical_url}
    count = await files_collection.count_documents(supersede_filter)
    prior_versions = await files_collection.distinct("version", supersede_filter)
    max_version = max([v for v in prior_versions if isinstance(v, int)], default=0)
    return {
        "filter": supersede_filter,
        "would_delete_chunks": count,
        "prior_versions": sorted(v for v in prior_versions if isinstance(v, int)),
        "next_version": max_version + 1,
    }


@router.get("/status")
async def web_ingest_status():
    _require_enabled()
    return {"enabled": True, "allowedDomains": config.WEB_INGEST_ALLOWED_DOMAINS}


@router.post("/preview")
@limiter.limit(config.RATE_LIMIT_UPLOAD, key_func=user_id_key)
async def web_preview(
    request: Request,
    body: WebPreviewRequest,
    current_user: User = Depends(rate_limit_identify),
):
    """Fetch + extract WITHOUT indexing: what would be stored, so the user can
    confirm. Also reports whether this URL is already in the KB (the UI offers
    Refresh instead of Add)."""
    _require_enabled()
    fetched, extracted = await _fetch_and_extract(body.url)
    result = build_web_document(extracted.text, extracted.title)
    page_text = result.pages[0][1] if result.pages else ""
    ok, reason = validate_web_document(result)
    warnings = list(extracted.warnings)
    if not ok:
        warnings.append(f"This page cannot be ingested as-is: {reason}.")

    existing = await _existing_web_doc(fetched.url)
    return {
        "resolvedUrl": fetched.url,
        "title": extracted.title,
        "preview": page_text[:PREVIEW_CHARS],
        "charCount": len(page_text),
        "textRatio": extracted.text_ratio,
        "sizeBytes": fetched.size_bytes,
        "ingestable": ok,
        "warnings": warnings,
        "alreadyIngested": (
            {
                "canonicalTitle": existing.get("canonicalTitle"),
                "fetchedAt": _iso(existing.get("fetchedAt")),
                "version": existing.get("version"),
            }
            if existing else None
        ),
    }


@router.post("/ingest")
@limiter.limit(config.RATE_LIMIT_UPLOAD, key_func=user_id_key)
async def web_ingest(
    request: Request,
    body: WebIngestRequest,
    current_user: User = Depends(rate_limit_identify),
):
    """Fetch, validate and index one page synchronously (a page is a handful
    of chunks — no background job or polling needed)."""
    _require_enabled()
    project = (body.project or "").strip()
    if not project:
        _fail(status.HTTP_400_BAD_REQUEST, "missing_project",
              "A project tag is required, same as any KB upload.")
    if not body.permissionConfirmed:
        _fail(status.HTTP_400_BAD_REQUEST, "permission_not_confirmed",
              "Please confirm this is a public page whose content may be stored "
              "in the knowledge base.")

    fetched, extracted = await _fetch_and_extract(body.url)
    canonical_url = fetched.url
    canonical_title = (body.title or "").strip() or extracted.title or canonical_url

    result = build_web_document(extracted.text, canonical_title)
    ok, reason = validate_web_document(result)
    if not ok:
        _fail(status.HTTP_422_UNPROCESSABLE_ENTITY, "no_usable_text",
              f"No usable text could be extracted from this page: {reason}. "
              f"If the page builds its content with JavaScript, it cannot be ingested.")

    pages = result.pages
    content_hash = val.normalized_text_hash(pages)

    existing = await _existing_web_doc(canonical_url)
    if existing and not body.refresh:
        _fail(status.HTTP_409_CONFLICT, "already_ingested",
              f"This URL is already in the knowledge base as "
              f"\"{existing.get('canonicalTitle')}\" (fetched "
              f"{_iso(existing.get('fetchedAt'))}). Use refresh to re-fetch and "
              f"replace it.",
              canonicalUrl=canonical_url,
              fetchedAt=_iso(existing.get("fetchedAt")))

    # Same content under a DIFFERENT URL (mirror, tracking-params variant).
    other = await files_collection.find_one(
        {"category": KB_CATEGORY, "contentHash": content_hash,
         "canonicalUrl": {"$ne": canonical_url}},
        {"canonicalTitle": 1, "canonicalUrl": 1},
    )
    if other:
        _fail(status.HTTP_409_CONFLICT, "duplicate_content",
              f"A document with identical content is already in the knowledge base "
              f"(\"{other.get('canonicalTitle') or other.get('canonicalUrl')}\").")

    if not val.reserve_hash(content_hash):
        _fail(status.HTTP_409_CONFLICT, "duplicate_content",
              "This page is being ingested right now. Please wait a moment.")
    try:
        if not ingest_try_acquire():
            _fail(status.HTTP_503_SERVICE_UNAVAILABLE, "busy",
                  "The server is busy indexing uploads. Please try again shortly.")
        try:
            plan = await _web_supersede_plan(canonical_url)
            version = plan["next_version"]
            previous_fetched_at = _iso(existing.get("fetchedAt")) if existing else None
            content_changed = (existing or {}).get("contentHash") != content_hash

            fetched_at = datetime.now()
            batch_id = uuid.uuid4().hex
            uploader_name = (getattr(current_user, "full_name", None)
                             or getattr(current_user, "email", "student"))
            prov = build_provenance(
                uploader_id=str(current_user.id), uploader_name=uploader_name,
                uploaded_at=fetched_at, project_tag=project, doc_type="web_page",
                source_format="web", batch_id=batch_id,
                canonical_title=canonical_title, version=version,
                permission_confirmed=True,
            )
            prov["contentHash"] = content_hash
            prov["canonicalUrl"] = canonical_url
            prov["fetchedAt"] = fetched_at

            if body.refresh and plan["would_delete_chunks"] > 0:
                print(f"[KB_WEB_SUPERSEDE] filter={plan['filter']} deleting "
                      f"{plan['would_delete_chunks']} prior-version chunk(s) for "
                      f"{canonical_url} before inserting v{version}")
                del_res = await files_collection.delete_many(plan["filter"])
                print(f"[KB_WEB_SUPERSEDE] deleted {del_res.deleted_count} chunk(s)")

            res = await ingest_document(
                canonical_url, b"", category=KB_CATEGORY,
                user_id=str(current_user.id), pre_extracted_pages=pages,
                provenance=prov,
            )
            chunk_count = res.get("chunks_created", 0)

            sample = await files_collection.find_one(
                {"category": KB_CATEGORY, "batchId": batch_id, "chunkIndex": 0},
                {"text": 1})
            await files_collection.insert_one({
                "docType": "kb_batch", "batchId": batch_id,
                "uploaderId": str(current_user.id), "uploaderName": uploader_name,
                "status": "indexed", "filename": canonical_url,
                "canonicalTitle": canonical_title, "projectTag": project,
                "version": version, "sourceFormat": "web",
                "canonicalUrl": canonical_url, "fetchedAt": fetched_at,
                "chunkCount": chunk_count,
                "sampleChunk": ((sample or {}).get("text") or "")[:400],
                "supersede": {"would_delete_chunks": plan["would_delete_chunks"],
                              "prior_versions": plan["prior_versions"]},
                "createdAt": fetched_at, "processedAt": datetime.now(),
            })
            await val.get_kb_centroid(force=True)
            await _audit("web_ingest", str(current_user.id), uploader_name, "endpoint",
                         batchId=batch_id, canonicalTitle=canonical_title,
                         canonicalUrl=canonical_url, project=project, version=version,
                         chunks=chunk_count, refresh=bool(body.refresh))
            return {
                "status": "indexed",
                "batchId": batch_id,
                "canonicalUrl": canonical_url,
                "canonicalTitle": canonical_title,
                "fetchedAt": _iso(fetched_at),
                "previousFetchedAt": previous_fetched_at,
                "contentChanged": content_changed,
                "version": version,
                "chunkCount": chunk_count,
                "superseded": plan["would_delete_chunks"] if body.refresh else 0,
            }
        finally:
            ingest_release()
    finally:
        val.release_hash(content_hash)


def register(app: FastAPI) -> None:
    """Include the routes ONLY when WEB_INGEST_ENABLED is on — with the flag
    off the routes are absent and the app's route table is unchanged."""
    if config.WEB_INGEST_ENABLED:
        app.include_router(router)
