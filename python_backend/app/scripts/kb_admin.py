"""
Knowledge Base Admin CLI.

Usage:
    python -m app.scripts.kb_admin <command> [options]

Commands:
    list      Show all knowledge base documents and a summary.
    reindex   Re-chunk KB documents using current v2 chunking strategy.
    add       Add a PDF (or folder of PDFs) to the knowledge base.
    remove    Remove a KB document (and all its chunks) by filename / regex.
    status    Show overall KB health and v1/v2 breakdown.

All commands accept --help. Run any command without args to see options.

This tool reuses the chunking, embedding, and ingestion logic from
app.services.rag_service so behavior stays consistent with the live app.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm
from rich.table import Table

from app.core.config import (
    CHUNKING_VERSION,
    KNOWLEDGE_BASE_PDF_PATH,
    USER_ID,
)
from app.core.database import files_collection, close_mongo_connection
from app.services.file_processing import (
    SUPPORTED_EXTENSIONS,
    get_file_type,
    is_supported_file,
)
from app.services.rag_service import (
    delete_document,
    ingest_document,
)

console = Console()

KB_CATEGORY = "knowledge_base"
USER_CATEGORY = "user_upload"


# ---------------------------------------------------------------------------
# Mongo helpers — all read/aggregate ops use the existing files_collection
# ---------------------------------------------------------------------------
async def _list_kb_files() -> List[Dict[str, Any]]:
    """Return one summary record per distinct KB filename."""
    pipeline = [
        {"$match": {"category": KB_CATEGORY, "userId": USER_ID}},
        {
            "$group": {
                "_id": "$filename",
                "chunk_count": {"$sum": 1},
                "versions": {"$addToSet": "$chunkingVersion"},
                "file_types": {"$addToSet": "$metadata.fileType"},
                "created_at": {"$min": "$createdAt"},
                "has_embedding": {
                    "$sum": {"$cond": [{"$ifNull": ["$embedding", False]}, 1, 0]}
                },
                "ocr_chunks": {
                    "$sum": {"$cond": [{"$eq": ["$metadata.ocrExtracted", True]}, 1, 0]}
                },
            }
        },
        {"$sort": {"_id": 1}},
    ]
    out: List[Dict[str, Any]] = []
    async for doc in files_collection.aggregate(pipeline):
        out.append(doc)
    return out


async def _category_counts(category: str) -> Dict[str, int]:
    """Total chunks + v1/v2 counts for a category."""
    pipeline = [
        {"$match": {"category": category, "userId": USER_ID}},
        {
            "$group": {
                "_id": None,
                "total_chunks": {"$sum": 1},
                "v2_chunks": {
                    "$sum": {"$cond": [{"$eq": ["$chunkingVersion", "v2"]}, 1, 0]}
                },
                "v1_chunks": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$chunkingVersion", "v1"]},
                                    {"$eq": [{"$ifNull": ["$chunkingVersion", None]}, None]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "missing_embedding": {
                    "$sum": {"$cond": [{"$ifNull": ["$embedding", False]}, 0, 1]}
                },
                "files": {"$addToSet": "$filename"},
            }
        },
    ]
    async for doc in files_collection.aggregate(pipeline):
        return {
            "total_chunks": doc.get("total_chunks", 0),
            "v1_chunks": doc.get("v1_chunks", 0),
            "v2_chunks": doc.get("v2_chunks", 0),
            "missing_embedding": doc.get("missing_embedding", 0),
            "file_count": len(doc.get("files", [])),
        }
    return {
        "total_chunks": 0,
        "v1_chunks": 0,
        "v2_chunks": 0,
        "missing_embedding": 0,
        "file_count": 0,
    }


async def _find_kb_chunk_for_filename(filename: str) -> Optional[Dict[str, Any]]:
    """Return any one chunk doc for the given KB filename (for content lookup)."""
    return await files_collection.find_one(
        {"category": KB_CATEGORY, "userId": USER_ID, "filename": filename}
    )


async def _filenames_matching_regex(pattern: str, category: str) -> List[str]:
    """Distinct filenames in a category matching the given regex."""
    names = await files_collection.distinct(
        "filename", {"category": category, "userId": USER_ID}
    )
    rx = re.compile(pattern)
    return sorted([n for n in names if n and rx.search(n)])


# ---------------------------------------------------------------------------
# PDF source resolution for reindex / add
# ---------------------------------------------------------------------------
def _resolve_pdf_bytes(filename: str, mongo_chunk: Optional[Dict[str, Any]]) -> Optional[bytes]:
    """
    Try Option A: a `content` field on a stored chunk (binary PDF).
    Then Option B: KNOWLEDGE_BASE_PDF_PATH/<filename>.
    Returns None if neither is available.
    """
    if mongo_chunk and mongo_chunk.get("content"):
        content = mongo_chunk["content"]
        if isinstance(content, (bytes, bytearray)):
            return bytes(content)

    if KNOWLEDGE_BASE_PDF_PATH:
        candidate = Path(KNOWLEDGE_BASE_PDF_PATH) / filename
        if candidate.is_file():
            return candidate.read_bytes()

    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def cmd_list(args: argparse.Namespace) -> int:
    files = await _list_kb_files()
    if not files:
        console.print("[yellow]No knowledge base documents found.[/yellow]")
        return 0

    table = Table(title="Knowledge Base Documents", show_lines=False)
    table.add_column("Filename", style="cyan", overflow="fold")
    table.add_column("Type", style="blue")
    table.add_column("Chunks", justify="right", style="green")
    table.add_column("OCR", justify="right", style="yellow")
    table.add_column("Versions", style="magenta")
    table.add_column("Added", style="white")

    total_chunks = 0
    v1 = v2 = 0
    type_counts: Dict[str, int] = {}
    for f in files:
        versions = sorted([v or "v1" for v in (f.get("versions") or [])])
        added = f.get("created_at")
        added_str = added.strftime("%Y-%m-%d %H:%M") if added else "—"
        types_in_meta = [t for t in (f.get("file_types") or []) if t]
        # Fall back to filename extension if metadata.fileType not stored
        ftype = (types_in_meta[0] if types_in_meta else get_file_type(str(f.get("_id", "")))) or "?"
        type_counts[ftype] = type_counts.get(ftype, 0) + 1
        ocr_chunks = int(f.get("ocr_chunks", 0) or 0)
        table.add_row(
            str(f.get("_id", "—")),
            ftype,
            str(f.get("chunk_count", 0)),
            str(ocr_chunks) if ocr_chunks else "—",
            ", ".join(versions) or "—",
            added_str,
        )
        total_chunks += f.get("chunk_count", 0)
        if "v2" in versions:
            v2 += 1
        if "v1" in versions or None in (f.get("versions") or []):
            v1 += 1

    console.print(table)
    type_breakdown = "  ".join(f"{t}:{n}" for t, n in sorted(type_counts.items()))
    console.print(
        f"\n[bold]Summary:[/bold] {len(files)} documents, "
        f"{total_chunks} chunks  "
        f"([magenta]v2:[/magenta] {v2}  [yellow]v1/legacy:[/yellow] {v1})"
    )
    if type_breakdown:
        console.print(f"[bold]By file type:[/bold] {type_breakdown}")
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    kb = await _category_counts(KB_CATEGORY)
    user = await _category_counts(USER_CATEGORY)
    kb_files = await _list_kb_files()
    empty_docs = [f for f in kb_files if f.get("chunk_count", 0) == 0]

    table = Table(title="KB Health")
    table.add_column("Metric", style="cyan")
    table.add_column("Knowledge Base", justify="right", style="green")
    table.add_column("User Uploads", justify="right", style="blue")
    table.add_row("Distinct files", str(kb["file_count"]), str(user["file_count"]))
    table.add_row("Total chunks", str(kb["total_chunks"]), str(user["total_chunks"]))
    table.add_row("v2 chunks", str(kb["v2_chunks"]), str(user["v2_chunks"]))
    table.add_row("v1/legacy chunks", str(kb["v1_chunks"]), str(user["v1_chunks"]))
    table.add_row(
        "Missing embeddings",
        str(kb["missing_embedding"]),
        str(user["missing_embedding"]),
    )
    console.print(table)

    if empty_docs:
        console.print(
            f"[yellow]Warning:[/yellow] {len(empty_docs)} KB files have 0 chunks."
        )
    if kb["missing_embedding"] > 0:
        console.print(
            f"[red]Alert:[/red] {kb['missing_embedding']} KB chunks missing embeddings."
        )
    if kb["v1_chunks"] > 0:
        console.print(
            f"[yellow]Note:[/yellow] {kb['v1_chunks']} legacy v1 chunks present — "
            f"consider running [bold]reindex[/bold]."
        )
    return 0


async def _add_one_file(
    pdf_path: Path,
    existing_filenames: set,
    skip_existing: bool,
    stats: Dict[str, Any],
    progress: Optional[Progress] = None,
) -> None:
    """
    Ingest a single file into the KB, updating `stats` in place.

    `stats` keys: added (int), total_chunks (int), skipped (int),
    failed (List[Tuple[filename, error]]).

    When the filename already exists in the KB:
      - skip_existing=True  -> log a yellow SKIP line and move on (no prompt)
      - skip_existing=False -> prompt to overwrite (backward-compatible)
    """
    filename = pdf_path.name

    if filename in existing_filenames:
        if skip_existing:
            console.print(f"[yellow]SKIP: {filename} (already in KB)[/yellow]")
            stats["skipped"] += 1
            return

        # Backward-compatible interactive overwrite prompt. Pause the live
        # progress display (if any) so the prompt renders cleanly.
        if progress is not None:
            progress.stop()
        try:
            overwrite = Confirm.ask(
                f"[yellow]'{filename}' already exists. Overwrite?[/yellow]",
                default=False,
            )
        finally:
            if progress is not None:
                progress.start()

        if not overwrite:
            console.print(f"[yellow]SKIP: {filename} (declined overwrite)[/yellow]")
            stats["skipped"] += 1
            return

        console.print(f"  Deleting existing chunks for {filename}...")
        await delete_document(filename)

    try:
        content = pdf_path.read_bytes()
        result = await ingest_document(filename, content, category=KB_CATEGORY)
        chunks = result["chunks_created"]
        console.print(
            f"[green]ADD: {filename}[/green] — {chunks} chunks "
            f"({result['total_characters']} chars, v{result['chunking_version']})"
        )
        stats["added"] += 1
        stats["total_chunks"] += chunks
    except Exception as e:
        console.print(f"[red]FAIL: {filename} — {e}[/red]")
        stats["failed"].append((filename, str(e)))


def _print_add_summary(stats: Dict[str, Any]) -> None:
    """Print the end-of-run summary table (always shown)."""
    failed = stats["failed"]

    table = Table(title="Add Summary", show_header=False, box=None, pad_edge=False)
    table.add_column("Result", style="bold")
    table.add_column("Detail")
    table.add_row(
        "[green]Added:[/green]",
        f"{stats['added']} files ({stats['total_chunks']} total chunks created)",
    )
    table.add_row(
        "[yellow]Skipped:[/yellow]",
        f"{stats['skipped']} files (already in KB)",
    )
    table.add_row(
        "[red]Failed:[/red]",
        f"{len(failed)} files",
    )

    console.print()
    console.print(table)

    if failed:
        console.print("\n[red]Failed files:[/red]")
        for fn, err in failed:
            console.print(f"  [red]•[/red] {fn}: {err}")


async def cmd_add(args: argparse.Namespace) -> int:
    paths: List[Path] = []
    if args.folder:
        folder = Path(args.folder)
        if not folder.is_dir():
            console.print(f"[red]Folder not found:[/red] {folder}")
            return 1
        # Discover every supported file type in the folder
        paths = sorted(
            p for p in folder.iterdir()
            if p.is_file() and is_supported_file(p.name)
        )
        if not paths:
            console.print(
                f"[yellow]No supported files in {folder}[/yellow]  "
                f"(supported: {sorted(SUPPORTED_EXTENSIONS)})"
            )
            return 0
    else:
        if not args.path:
            console.print("[red]Provide a file path or use --folder.[/red]")
            return 1
        p = Path(args.path)
        if not p.is_file():
            console.print(f"[red]File not found:[/red] {p}")
            return 1
        if not is_supported_file(p.name):
            console.print(
                f"[red]Unsupported file type:[/red] {get_file_type(p.name)}  "
                f"(supported: {sorted(SUPPORTED_EXTENSIONS)})"
            )
            return 1
        paths = [p]

    # Query existing KB filenames ONCE up front. Querying per-file would be far
    # too slow for large batches (173+ files).
    existing_filenames = set(
        await files_collection.distinct(
            "filename", {"category": KB_CATEGORY, "userId": USER_ID}
        )
    )

    stats: Dict[str, Any] = {
        "added": 0,
        "total_chunks": 0,
        "skipped": 0,
        "failed": [],  # List[Tuple[filename, error]]
    }

    if args.folder:
        # Show a progress bar for batch (folder) adds so large runs are visible.
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Adding", total=len(paths))
            for pdf_path in paths:
                progress.update(task, description=f"Adding {pdf_path.name[:40]}")
                await _add_one_file(
                    pdf_path,
                    existing_filenames,
                    args.skip_existing,
                    stats,
                    progress,
                )
                progress.advance(task)
    else:
        for pdf_path in paths:
            await _add_one_file(
                pdf_path,
                existing_filenames,
                args.skip_existing,
                stats,
                None,
            )

    _print_add_summary(stats)
    return 0 if not stats["failed"] else 1


async def cmd_remove(args: argparse.Namespace) -> int:
    pattern = args.pattern
    matches = await _filenames_matching_regex(pattern, KB_CATEGORY)
    if not matches:
        console.print(f"[yellow]No KB filenames match:[/yellow] {pattern}")
        return 0

    table = Table(title=f"Files matching '{pattern}'")
    table.add_column("Filename", style="cyan")
    table.add_column("Chunks", justify="right", style="green")
    counts = {}
    for fn in matches:
        c = await files_collection.count_documents(
            {"category": KB_CATEGORY, "userId": USER_ID, "filename": fn}
        )
        counts[fn] = c
        table.add_row(fn, str(c))
    console.print(table)

    total_chunks = sum(counts.values())
    console.print(
        f"\n[red]This will delete {len(matches)} files and {total_chunks} chunks.[/red]"
    )
    if not Confirm.ask("Proceed?", default=False):
        console.print("[yellow]Aborted.[/yellow]")
        return 0

    deleted = 0
    for fn in matches:
        try:
            res = await delete_document(fn)
            deleted += res.get("deleted_count", 0)
            console.print(f"  [green]removed[/green] {fn} ({res['deleted_count']} chunks)")
        except Exception as e:
            console.print(f"  [red]failed[/red] {fn}: {e}")
    console.print(f"\n[bold]Deleted {deleted} chunks total.[/bold]")
    return 0


async def cmd_reindex(args: argparse.Namespace) -> int:
    if args.filename:
        targets = [args.filename]
    else:
        targets = sorted(
            await files_collection.distinct(
                "filename", {"category": KB_CATEGORY, "userId": USER_ID}
            )
        )
    if not targets:
        console.print("[yellow]No KB documents to reindex.[/yellow]")
        return 0

    console.print(f"[bold]Reindexing {len(targets)} document(s)...[/bold]")
    if args.dry_run:
        console.print("[yellow]--dry-run: no changes will be made[/yellow]")

    if not KNOWLEDGE_BASE_PDF_PATH:
        console.print(
            "[yellow]KNOWLEDGE_BASE_PDF_PATH is not set — will only work if PDF "
            "binaries are stored in Mongo (content field).[/yellow]"
        )

    ok = skipped = failed = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Reindexing", total=len(targets))
        for fn in targets:
            progress.update(task, description=f"Reindexing {fn[:48]}")
            try:
                sample = await _find_kb_chunk_for_filename(fn)
                pdf_bytes = _resolve_pdf_bytes(fn, sample)
                if pdf_bytes is None:
                    console.print(
                        f"  [yellow]skip[/yellow] {fn} — original PDF not found "
                        f"(no `content` field and not in KNOWLEDGE_BASE_PDF_PATH)"
                    )
                    skipped += 1
                    progress.advance(task)
                    continue

                if args.dry_run:
                    console.print(
                        f"  [cyan]would reindex[/cyan] {fn} "
                        f"({len(pdf_bytes)} bytes)"
                    )
                    ok += 1
                    progress.advance(task)
                    continue

                await delete_document(fn)
                result = await ingest_document(fn, pdf_bytes, category=KB_CATEGORY)
                console.print(
                    f"  [green]OK[/green] {fn} -> {result['chunks_created']} chunks"
                )
                ok += 1
            except Exception as e:
                console.print(f"  [red]FAILED[/red] {fn}: {e}")
                failed += 1
            progress.advance(task)

    console.print(
        f"\n[bold]Reindex summary:[/bold] ok={ok} skipped={skipped} failed={failed}  "
        f"(chunking version: {CHUNKING_VERSION})"
    )
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kb_admin",
        description="Knowledge Base administration CLI",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all KB documents")
    sub.add_parser("status", help="Show KB health summary")

    re_p = sub.add_parser("reindex", help="Re-chunk KB documents with current v2 chunker")
    re_p.add_argument("--filename", help="Reindex only this filename")
    re_p.add_argument("--dry-run", action="store_true", help="Preview without changes")

    add_p = sub.add_parser(
        "add",
        help="Add a file (or folder of files) to the KB",
        description=(
            "Add one file, or every supported file in a folder, to the "
            "knowledge base. By default you are prompted before overwriting a "
            "file whose name is already in the KB; use --skip-existing to skip "
            "such files silently instead. A summary of added/skipped/failed "
            "files is printed at the end of every run."
        ),
    )
    add_p.add_argument("path", nargs="?", help="Path to a single file to add")
    add_p.add_argument(
        "--folder",
        help="Add every supported file inside this folder (shows a progress bar)",
    )
    add_p.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip files whose filename is already in the KB instead of "
            "prompting to overwrite. Logs 'SKIP: <filename> (already in KB)' "
            "and moves on. Works with single-file and --folder mode."
        ),
    )

    rm_p = sub.add_parser("remove", help="Remove KB docs matching a filename regex")
    rm_p.add_argument("pattern", help="Regex matched against filename")

    return p


COMMANDS = {
    "list": cmd_list,
    "status": cmd_status,
    "reindex": cmd_reindex,
    "add": cmd_add,
    "remove": cmd_remove,
}


async def _main_async(args: argparse.Namespace) -> int:
    try:
        return await COMMANDS[args.command](args)
    finally:
        await close_mongo_connection()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        rc = asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
