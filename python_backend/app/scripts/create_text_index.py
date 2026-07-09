"""
Additive Atlas Search index creator — `text_index` for BM25 hybrid search.

Creates a NEW full-text (Lucene BM25) search index named ``text_index`` on the
files collection, ALONGSIDE the existing ``vector_index``. This is purely
ADDITIVE: it does not touch vector_index, does not modify any of the 16,811 KB
chunks, and does not change retrieval behavior until HYBRID_SEARCH_ENABLED is
turned on in config.

Index definition (dynamic:false — ONLY these three fields are indexed, so the
384-dim ``embedding`` array is never touched by this index):
    text     : string  -> BM25-scored full-text field (the chunk body)
    category : token    -> exact-match scope filter (knowledge_base/user_upload)
    userId   : token    -> exact-match per-user upload scoping

IDEMPOTENT: checks whether ``text_index`` already exists first and no-ops if so.
Safe to re-run. This WRITES to the shared Atlas cluster, so run it deliberately.

Run (from the python_backend directory):
    python -m app.scripts.create_text_index
"""
from __future__ import annotations

import asyncio
from typing import List

from pymongo.operations import SearchIndexModel

from app.core.database import files_collection, close_mongo_connection

INDEX_NAME = "text_index"

# dynamic:false keeps the index minimal and additive — only the three fields
# below are indexed; nothing else in the document (notably `embedding`) is.
INDEX_DEFINITION = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "text": {"type": "string"},
            "category": {"type": "token"},
            "userId": {"type": "token"},
        },
    }
}


async def _existing_index_names() -> List[str]:
    names: List[str] = []
    async for ix in files_collection.list_search_indexes():
        names.append(ix.get("name"))
    return names


async def main() -> int:
    try:
        print("[text_index] Checking existing search indexes on 'files'...")
        existing = await _existing_index_names()
        print(f"[text_index] Found: {existing or '(none)'}")

        if INDEX_NAME in existing:
            print(
                f"[text_index] '{INDEX_NAME}' already exists — no-op. "
                "Nothing to do."
            )
            return 0

        print(f"[text_index] Creating additive search index '{INDEX_NAME}'...")
        model = SearchIndexModel(
            definition=INDEX_DEFINITION,
            name=INDEX_NAME,
            type="search",
        )
        result = await files_collection.create_search_index(model)
        print(f"[text_index] Create submitted (returned name: {result}).")
        print(
            "[text_index] Atlas builds the index asynchronously; it typically "
            "becomes queryable within ~1-2 min. Re-run this script (it will "
            "report the index already exists) or check the Atlas UI for "
            "status READY / queryable True before enabling HYBRID_SEARCH_ENABLED."
        )
        return 0
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
