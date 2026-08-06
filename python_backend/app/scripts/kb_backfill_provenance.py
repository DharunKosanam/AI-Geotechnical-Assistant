"""Phase 1 backfill: stamp provenance onto the existing knowledge_base corpus.

Dry-run by DEFAULT: prints the filter, counts, and the exact $set it would apply
(with a per-filename sample) and writes nothing. Pass --apply to execute.

The write is ADDITIVE (only sets the provenance fields; touches no existing
field) and IDEMPOTENT (re-running sets the same values), so it is safe to run
against the live shared KB — retrieval indexes only the embedding/text fields,
never these, so behaviour is unchanged.

Run from python_backend:
    ./venv/bin/python -m app.scripts.kb_backfill_provenance            # dry-run
    ./venv/bin/python -m app.scripts.kb_backfill_provenance --apply    # execute
"""
import argparse
import asyncio
from datetime import datetime
from typing import Any, Dict, List

from app.core.database import files_collection
from app.services.rag_service import get_clean_title
from app.services.kb_provenance import (
    LEGACY_BATCH_ID,
    LEGACY_DOC_TYPE,
    LEGACY_PROJECT_TAG,
    LEGACY_UPLOADER_ID,
    LEGACY_UPLOADER_NAME,
    source_format_of,
)

KB = {"category": "knowledge_base"}


async def _plan() -> List[Dict[str, Any]]:
    """Per-filename plan: how many docs, and the canonicalTitle / sourceFormat
    those docs will receive."""
    filenames = await files_collection.distinct("filename", KB)
    plan: List[Dict[str, Any]] = []
    for fn in sorted(filenames, key=lambda x: x or ""):
        count = await files_collection.count_documents({**KB, "filename": fn})
        plan.append({
            "filename": fn,
            "count": count,
            "canonicalTitle": get_clean_title(fn or "")["title"],
            "sourceFormat": source_format_of(fn),
        })
    return plan


def _constant_fields() -> Dict[str, Any]:
    return {
        "uploaderId": LEGACY_UPLOADER_ID,
        "uploaderName": LEGACY_UPLOADER_NAME,
        "projectTag": LEGACY_PROJECT_TAG,
        "docType": LEGACY_DOC_TYPE,
        "batchId": LEGACY_BATCH_ID,
        "version": 1,
        "permissionConfirmed": True,
    }


async def dry_run() -> None:
    total = await files_collection.count_documents(KB)
    already = await files_collection.count_documents({**KB, "uploaderId": {"$exists": True}})
    plan = await _plan()
    would_update = sum(p["count"] for p in plan)

    print(f"[DRY-RUN] filter: {KB}")
    print(f"[DRY-RUN] KB docs total: {total}")
    print(f"[DRY-RUN] distinct filenames: {len(plan)}")
    print(f"[DRY-RUN] already carry uploaderId (idempotent overwrite): {already}")
    print(f"[DRY-RUN] constant $set: {_constant_fields()}")
    print("[DRY-RUN] uploadedAt <- doc.createdAt (fallback: migration timestamp)")
    print("[DRY-RUN] per-filename canonicalTitle + sourceFormat, sample of 10:")
    for p in plan[:10]:
        print(f"    {p['count']:6d}  fmt={p['sourceFormat']:>5s}  "
              f"title={p['canonicalTitle'][:58]!r}")
    print(f"[DRY-RUN] docs that WOULD be updated: {would_update}")
    print("[DRY-RUN] no writes performed. Re-run with --apply to execute.")


async def apply() -> None:
    plan = await _plan()
    migration_ts = datetime.now()

    # 1. uploadedAt <- createdAt for every KB doc, in one pipeline update.
    r1 = await files_collection.update_many(
        KB,
        [{"$set": {"uploadedAt": {"$ifNull": ["$createdAt", migration_ts]}}}],
    )
    print(f"[APPLY] uploadedAt set on {r1.modified_count} docs")

    # 2. Per-filename plain $set for the remaining 9 fields (plain $set treats
    #    every value as a literal, so titles with odd characters are safe).
    const = _constant_fields()
    updated = 0
    for p in plan:
        fields = {**const,
                  "sourceFormat": p["sourceFormat"],
                  "canonicalTitle": p["canonicalTitle"]}
        res = await files_collection.update_many(
            {**KB, "filename": p["filename"]},
            {"$set": fields},
        )
        updated += res.modified_count
    print(f"[APPLY] provenance set on {updated} docs across {len(plan)} filenames")

    # 3. Verify.
    have = await files_collection.count_documents({**KB, "uploaderId": {"$exists": True}})
    missing = await files_collection.count_documents({**KB, "uploaderId": {"$exists": False}})
    have_ts = await files_collection.count_documents({**KB, "uploadedAt": {"$exists": True}})
    print(f"[VERIFY] KB docs with uploaderId: {have}; missing: {missing}; with uploadedAt: {have_ts}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill provenance onto KB docs (dry-run by default).")
    ap.add_argument("--apply", action="store_true", help="perform the backfill (default: dry-run only)")
    args = ap.parse_args()
    asyncio.run(apply() if args.apply else dry_run())


if __name__ == "__main__":
    main()
