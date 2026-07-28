"""Phase 6 KB removal CLI — remove uploaded KB material by batch, uploader, or
project. Adjacent to (and deliberately separate from) the curated-KB kb_admin.py.

DRY-RUN IS MANDATORY: without --apply it only prints the filter and the counts it
WOULD delete, and writes nothing. --apply performs the delete and records a
kb_audit entry.

Safety: the filters are exact. Note that the legacy backfill tagged every
pre-existing KB doc with projectTag="legacy" / uploaderId="system", so
`--project legacy` or `--uploader system` would match the ENTIRE legacy corpus —
the dry-run count makes that obvious before you ever pass --apply.

Run from python_backend:
    ./venv/bin/python -m app.scripts.kb_remove --batch <id>            # dry-run
    ./venv/bin/python -m app.scripts.kb_remove --project "Site B" --apply
    ./venv/bin/python -m app.scripts.kb_remove --uploader <userId> --apply
"""
import argparse
import asyncio
from datetime import datetime
from typing import Any, Dict, Tuple

from app.core.database import files_collection, kb_audit_collection

KB = {"category": "knowledge_base"}


def _filters(args) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """(description, chunk_filter, batch_record_filter) for the chosen selector."""
    if args.batch:
        return (f"batch={args.batch}",
                {**KB, "batchId": args.batch},
                {"docType": "kb_batch", "batchId": args.batch})
    if args.uploader:
        return (f"uploader={args.uploader}",
                {**KB, "uploaderId": args.uploader},
                {"docType": "kb_batch", "uploaderId": args.uploader})
    return (f"project={args.project}",
            {**KB, "projectTag": args.project},
            {"docType": "kb_batch", "projectTag": args.project})


async def _run(desc: str, chunk_filter: Dict[str, Any], batch_filter: Dict[str, Any],
               apply: bool, actor: str) -> None:
    chunks = await files_collection.count_documents(chunk_filter)
    batches = await files_collection.count_documents(batch_filter)
    print(f"[DRY-RUN] selector: {desc}")
    print(f"[DRY-RUN] chunk filter:  {chunk_filter}")
    print(f"[DRY-RUN] would delete {chunks} KB chunk(s) across {batches} batch record(s)")

    # Surface an accidental whole-legacy-corpus match loudly.
    if chunk_filter.get("projectTag") == "legacy" or chunk_filter.get("uploaderId") == "system":
        print("[DRY-RUN] WARNING: this selector matches the LEGACY backfilled corpus.")

    if not apply:
        print("[DRY-RUN] nothing deleted. Re-run with --apply to execute.")
        return

    cres = await files_collection.delete_many(chunk_filter)
    bres = await files_collection.delete_many(batch_filter)
    await kb_audit_collection.insert_one({
        "action": "delete", "source": "cli", "actorName": actor, "selector": desc,
        "chunks": cres.deleted_count, "batches": bres.deleted_count, "at": datetime.now(),
    })
    print(f"[APPLY] deleted {cres.deleted_count} chunk(s) and {bres.deleted_count} batch record(s); audit written.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Remove KB uploads (dry-run unless --apply).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--batch", help="remove one upload batch by id")
    g.add_argument("--uploader", help="remove all uploads by this uploaderId")
    g.add_argument("--project", help="remove all uploads tagged with this project")
    ap.add_argument("--apply", action="store_true", help="perform the delete (default: dry-run)")
    ap.add_argument("--actor", default="cli-admin", help="name recorded in the audit log")
    args = ap.parse_args()
    desc, chunk_filter, batch_filter = _filters(args)
    asyncio.run(_run(desc, chunk_filter, batch_filter, args.apply, args.actor))


if __name__ == "__main__":
    main()
