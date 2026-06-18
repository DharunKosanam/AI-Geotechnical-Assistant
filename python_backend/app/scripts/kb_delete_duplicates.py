"""
Knowledge Base duplicate CLEANUP (destructive) -- Problem 3, Part A companion.

Executes a human-curated deletion plan against the knowledge base. This is the
destructive counterpart to kb_find_duplicates.py: that script identifies
suspected duplicates (read-only); this one removes the ones a human chose.

    python -m app.scripts.kb_delete_duplicates              (dry-run, default)
    python -m app.scripts.kb_delete_duplicates --execute    (real delete)

=========================  SAFE BY DEFAULT  =========================
- Dry-run unless --execute is passed.
- Refuses to run unless deletion_plan.txt exists and lists >=1 filename.
- Bulk-deletion guard: refuses plans larger than MAX_PLAN_SIZE.
- Fails CLOSED if any planned filename matches zero chunks (typo-catcher):
  never a partial delete.
- --execute additionally requires typing 'DELETE' exactly.
- After a successful execute, the plan is archived so it cannot be re-run.
====================================================================

The plan is a human artifact; this script's only job is to apply it safely and
reversibly-by-archive. It does not decide what to delete.

Deletes are scoped to {filename, category=knowledge_base, userId} -- the SAME
query used for the pre-flight count, so "chunks to delete" always equals what is
actually removed, and user_upload chunks that happen to share a name are never
touched.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from app.core.config import USER_ID
from app.core.database import files_collection, close_mongo_connection

KB_CATEGORY = "knowledge_base"

PLAN_FILENAME = "deletion_plan.txt"

# Bulk-deletion guard. 30 is generous for one cleanup cycle. RAISING THIS
# DEFEATS THE SAFETY CHECK -- only change it if you have consciously decided a
# single run legitimately needs to delete more than 30 documents.
MAX_PLAN_SIZE = 30

# Display width of the filename column in the summary table.
NAME_COL = 61

# Plain-ASCII guard for cp1252 consoles (no em-dash / smart quotes anywhere).
def _safe_print(message: str = "") -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(message.encode(enc, errors="backslashreplace").decode(enc))


def _ascii(text: str) -> str:
    return (text or "").encode("ascii", "ignore").decode("ascii")


def _plan_path() -> Path:
    """Same resolution convention as kb_find_duplicates.py's output file."""
    return Path(PLAN_FILENAME).resolve()


def _kb_query(filename: str) -> Dict[str, str]:
    """The one query used for BOTH counting and deleting a planned file."""
    return {"filename": filename, "category": KB_CATEGORY, "userId": USER_ID}


def _load_plan(path: Path) -> List[str]:
    """Read filenames from the plan: strip lines, skip blanks and # comments."""
    filenames: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        filenames.append(line)
    return filenames


def _truncate(name: str, width: int = NAME_COL) -> str:
    name = _ascii(name)
    return name if len(name) <= width else name[: width - 3] + "..."


def _print_summary_table(
    found: List[Tuple[str, int]],
    total_files: int,
    total_chunks: int,
) -> Tuple[int, int]:
    """Render the plan table; return (files_to_delete, chunks_to_delete)."""
    n_files = len(found)
    x_chunks = sum(c for _, c in found)

    sep = f"{'-' * NAME_COL} | {'-' * 7}"
    _safe_print(f"{'Filename':<{NAME_COL}} | {'Chunks'}")
    _safe_print(sep)
    for filename, count in found:
        _safe_print(f"{_truncate(filename):<{NAME_COL}} | {count}")
    _safe_print(sep)
    _safe_print(f"TOTAL: {n_files} files, {x_chunks} chunks to delete")
    _safe_print(
        f"Current KB:  {total_chunks} total chunks across {total_files} files"
    )
    _safe_print(
        f"After delete: {total_chunks - x_chunks} chunks across "
        f"{total_files - n_files} files"
    )
    return n_files, x_chunks


async def _kb_totals() -> Tuple[int, int]:
    """(total_files, total_chunks) currently in the KB."""
    base = {"category": KB_CATEGORY, "userId": USER_ID}
    total_chunks = await files_collection.count_documents(base)
    total_files = len(await files_collection.distinct("filename", base))
    return total_files, total_chunks


async def _preflight(filenames: List[str]) -> Tuple[List[Tuple[str, int]], List[str]]:
    """Count chunks per planned filename. Returns (found[(name,count)], missing[])."""
    found: List[Tuple[str, int]] = []
    missing: List[str] = []
    for filename in filenames:
        count = await files_collection.count_documents(_kb_query(filename))
        if count > 0:
            found.append((filename, count))
        else:
            _safe_print(f"[WARN] '{filename}' not found in KB")
            missing.append(filename)
    return found, missing


def _read_confirmation() -> str:
    """Read one line from stdin; '' if stdin is closed."""
    try:
        return input()
    except EOFError:
        return ""


async def _execute(found: List[Tuple[str, int]], plan_path: Path) -> int:
    n_files = len(found)
    x_chunks = sum(c for _, c in found)

    _safe_print("")
    _safe_print(
        f"[WARN] About to delete {x_chunks} chunks across {n_files} files. "
        f"This cannot be undone."
    )
    _safe_print("[WARN] Type 'DELETE' to confirm (case-sensitive):")
    if _read_confirmation() != "DELETE":
        _safe_print("[INFO] Aborted by user. No changes made.")
        return 0

    for filename, _ in found:
        result = await files_collection.delete_many(_kb_query(filename))
        _safe_print(f"[DELETE] {filename}: removed {result.deleted_count} chunks")

    # Verify nothing remains for any planned file.
    leftovers = 0
    for filename, _ in found:
        remaining = await files_collection.count_documents(_kb_query(filename))
        if remaining != 0:
            leftovers += 1
            _safe_print(
                f"[ERROR] {filename} still has {remaining} chunks - "
                f"manual cleanup needed"
            )

    new_files, new_chunks = await _kb_totals()
    # Pre-delete totals reconstructed from post-delete state + what we removed.
    _safe_print(
        f"[OK] KB now has {new_chunks} total chunks across {new_files} files "
        f"(was {new_chunks + x_chunks} / {new_files + n_files})"
    )

    # Step 5: archive the applied plan so it cannot be accidentally re-run.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = plan_path.with_name(f"deletion_plan_{stamp}.applied.txt")
    try:
        plan_path.rename(archive)
        _safe_print(f"[OK] Plan archived as {archive.name}")
    except OSError as exc:
        _safe_print(f"[WARN] Could not archive plan ({exc}); delete already applied.")

    return 1 if leftovers else 0


async def _run(execute: bool) -> int:
    plan_path = _plan_path()

    # Step 1: load + validate the plan.
    if not plan_path.exists():
        _safe_print(
            f"[ERROR] {PLAN_FILENAME} not found at {plan_path}. Create it first."
        )
        return 1

    filenames = _load_plan(plan_path)
    if not filenames:
        _safe_print(f"[ERROR] {PLAN_FILENAME} has no filenames. Nothing to delete.")
        return 1

    if len(filenames) > MAX_PLAN_SIZE:
        _safe_print(
            f"[ERROR] Plan has {len(filenames)} filenames - exceeds safety cap "
            f"of {MAX_PLAN_SIZE}."
        )
        _safe_print("[ERROR] Split into multiple runs or override cap in code.")
        return 1

    _safe_print(f"[INFO] Loaded {len(filenames)} filenames from {plan_path}")
    if not execute:
        _safe_print("[INFO] Mode: DRY-RUN (no --execute flag)")
    else:
        _safe_print("[INFO] Mode: EXECUTE (--execute flag set)")

    # Step 2: pre-flight against the KB. Fail closed on ANY missing filename.
    found, missing = await _preflight(filenames)
    if missing:
        _safe_print(
            f"[ERROR] {len(missing)} filenames in plan do not match any chunks "
            f"in the KB:"
        )
        for name in missing:
            _safe_print(f"  - {name}")
        _safe_print("[ERROR] Fix typos or remove from plan, then re-run.")
        return 1

    # Step 3: summary table (always).
    total_files, total_chunks = await _kb_totals()
    _safe_print("")
    _print_summary_table(found, total_files, total_chunks)
    _safe_print("")

    # Step 4: branch on mode.
    if not execute:
        _safe_print("[INFO] Dry-run mode - no changes made.")
        _safe_print("[INFO] Run with --execute to apply this plan.")
        return 0

    return await _execute(found, plan_path)


async def _main_async(execute: bool) -> int:
    try:
        return await _run(execute)
    finally:
        await close_mongo_connection()


def main() -> None:
    # Deliberately minimal arg handling: the ONLY recognized flag is --execute.
    # Anything else is ignored so a stray arg can never silently trigger a delete.
    execute = "--execute" in sys.argv[1:]
    try:
        rc = asyncio.run(_main_async(execute))
    except KeyboardInterrupt:
        _safe_print("\n[INFO] Interrupted. No changes made.")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
