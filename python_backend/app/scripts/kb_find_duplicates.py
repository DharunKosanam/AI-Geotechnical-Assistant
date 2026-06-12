"""
Knowledge Base duplicate-finder (diagnostic) -- Problem 3, Part A.

Finds SUSPECTED duplicate documents in the knowledge base by comparing a
per-document text fingerprint (probable title + start of the first chunk) and
writes a markdown report. This is identification ONLY: it does not remove or
prevent duplicates (those are separate follow-up tasks).

    python -m app.scripts.kb_find_duplicates

=========================  READ ONLY  =========================
This script performs NO writes/updates/deletes against MongoDB. It only reads
existing chunk data and writes a local markdown file (./suspected_duplicates.md).
===============================================================

Similarity: a pure-stdlib token_set_ratio (difflib-based) -- the same algorithm
as rapidfuzz.token_set_ratio but without adding a dependency (rapidfuzz is not
installed). token_set_ratio is robust to one side carrying extra boilerplate
(journal header / DOI / received dates on a published version vs a preprint),
which plain word-Jaccard is not. A cheap token-overlap prefilter skips the
obvious non-matches so ~20k pairs finish well under a second.
"""
from __future__ import annotations

import asyncio
import difflib
import re
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import USER_ID
from app.core.database import files_collection, close_mongo_connection

KB_CATEGORY = "knowledge_base"

# Classification thresholds (on token_set_ratio, 0..1).
SIM_HIGH = 0.90          # >= this -> HIGH CONFIDENCE
SIM_LOW = 0.70           # >= this (and < HIGH) -> BORDERLINE; below this -> ignored

# Fingerprint sizing.
FINGERPRINT_CHARS = 1000     # chars of the first chunk appended after the title
SNIPPET_CHARS = 200          # "first N chars of doc" shown in the report
TITLE_MIN_LEN = 10           # a title candidate line must be > this many chars
TITLE_MAX_LEN = 300          # ... and < this many chars

# Performance short-circuit: only run the (more expensive) token_set_ratio when
# the smaller token set is at least this fraction shared. This is a provably
# safe superset of the spec's "skip if titles share 0 tokens" idea -- any pair
# that could reach SIM_LOW=0.70 necessarily has overlap well above 0.35, so no
# real candidate is dropped here.
PREFILTER_OVERLAP = 0.35

OUTPUT_FILENAME = "suspected_duplicates.md"

# Map the unicode punctuation that commonly shows up in PDF text to ASCII so the
# report (and any console echo) stays cp1252-safe.
_UNICODE_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "ﬁ": "fi", "ﬂ": "fl",
}

_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")


def _safe_print(message: str) -> None:
    """print() that survives a cp1252 console when text contains non-ASCII."""
    try:
        print(message)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(message.encode(enc, errors="backslashreplace").decode(enc))


def _ascii(text: str) -> str:
    """Down-convert to plain ASCII (smart quotes/dashes -> ASCII, drop the rest)."""
    if not text:
        return ""
    for bad, good in _UNICODE_MAP.items():
        text = text.replace(bad, good)
    return text.encode("ascii", "ignore").decode("ascii")


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _norm_tokens(text: str) -> set:
    """Lowercase, strip punctuation, collapse whitespace -> set of word tokens."""
    return set(_PUNCT_RE.sub(" ", (text or "").lower()).split())


def _extract_title(first_text: str, filename: str) -> str:
    """First non-empty line in (TITLE_MIN_LEN, TITLE_MAX_LEN); else clean filename."""
    for line in (first_text or "").splitlines():
        stripped = _collapse_ws(line)
        if TITLE_MIN_LEN < len(stripped) < TITLE_MAX_LEN:
            return stripped
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return _collapse_ws(base.replace("_", " ").replace("-", " "))


def _token_set_ratio(a_tokens: set, b_tokens: set) -> float:
    """Pure-stdlib equivalent of rapidfuzz.token_set_ratio, scaled to 0..1.

    Builds the sorted intersection plus each side's remainder, then takes the
    best difflib ratio among (intersection vs each combined string) and
    (combined vs combined). High when the two share a large common core even if
    one side carries extra tokens.
    """
    inter = a_tokens & b_tokens
    if not inter:
        return 0.0
    sect = " ".join(sorted(inter))
    rest_a = " ".join(sorted(a_tokens - b_tokens))
    rest_b = " ".join(sorted(b_tokens - a_tokens))
    combined_a = (sect + " " + rest_a).strip()
    combined_b = (sect + " " + rest_b).strip()
    sm = difflib.SequenceMatcher
    return max(
        sm(None, sect, combined_a).ratio(),
        sm(None, sect, combined_b).ratio(),
        sm(None, combined_a, combined_b).ratio(),
    )


def build_fingerprint(first_text: str, filename: str) -> Dict[str, Any]:
    """Build the comparable fingerprint for one document from its first-page /
    first-chunk text.

    Shared by the diagnostic (existing KB docs, text from the first stored chunk)
    and the index-time check in kb_admin (a not-yet-indexed PDF, text from the
    raw file). Both sides go through the SAME title extraction and token
    normalization here, so extraction differences wash out before comparison.

    Returns ``{"title", "snippet", "tokens"}`` -- ``tokens`` is what
    ``_token_set_ratio`` consumes.
    """
    title = _extract_title(first_text, filename)
    fingerprint_text = title + " " + first_text[:FINGERPRINT_CHARS]
    return {
        "title": title,
        "snippet": _collapse_ws(first_text)[:SNIPPET_CHARS],
        "tokens": _norm_tokens(fingerprint_text),
    }


async def _load_documents() -> List[Dict[str, Any]]:
    """One record per KB filename: first chunk text, chunk count, first-indexed.

    The first chunk is the lowest pageStart then lowest chunkIndex (sorted before
    the $group so $first picks it). Legacy v1 chunks without those fields sort as
    null/first, which is fine -- they are still content chunks.
    """
    pipeline = [
        {"$match": {"category": KB_CATEGORY, "userId": USER_ID}},
        {"$sort": {"filename": 1, "pageStart": 1, "chunkIndex": 1}},
        {
            "$group": {
                "_id": "$filename",
                "first_text": {"$first": "$text"},
                "chunk_count": {"$sum": 1},
                "first_indexed": {"$min": "$createdAt"},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    docs: List[Dict[str, Any]] = []
    cursor = files_collection.aggregate(pipeline, allowDiskUse=True)
    async for row in cursor:
        filename = row.get("_id") or "(unknown)"
        first_text = row.get("first_text") or ""
        fp = build_fingerprint(first_text, filename)
        indexed: Optional[datetime] = row.get("first_indexed")
        docs.append(
            {
                "filename": filename,
                "title": fp["title"],
                "snippet": fp["snippet"],
                "chunk_count": int(row.get("chunk_count", 0) or 0),
                "first_indexed": indexed.strftime("%Y-%m-%d") if indexed else None,
                "tokens": fp["tokens"],
            }
        )
    return docs


def _find_pairs(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """All pairs scoring >= SIM_LOW, each tagged high/borderline."""
    pairs: List[Dict[str, Any]] = []
    for a, b in combinations(docs, 2):
        ta, tb = a["tokens"], b["tokens"]
        if not ta or not tb:
            continue
        # Cheap prefilter: skip pairs that cannot reach SIM_LOW (see comment on
        # PREFILTER_OVERLAP). This is what keeps ~20k pairs fast.
        inter = len(ta & tb)
        if inter == 0:
            continue
        if inter / min(len(ta), len(tb)) < PREFILTER_OVERLAP:
            continue
        score = _token_set_ratio(ta, tb)
        if score < SIM_LOW:
            continue
        pairs.append(
            {
                "a": a,
                "b": b,
                "score": score,
                "tier": "high" if score >= SIM_HIGH else "borderline",
            }
        )
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs


def _format_doc_block(label: str, doc: Dict[str, Any]) -> List[str]:
    title = _ascii(doc["title"]) or "(not available)"
    snippet = _ascii(doc["snippet"]) or "(not available)"
    indexed = doc["first_indexed"] or "(not available)"
    return [
        f"**[{label}]** `{_ascii(doc['filename'])}`",
        f"- Title: \"{title}\"",
        f"- Chunks in KB: {doc['chunk_count']}",
        f"- First indexed: {indexed}",
        f"- First {SNIPPET_CHARS} chars of doc: \"{snippet}\"",
        "",
    ]


def _build_report(docs: List[Dict[str, Any]], pairs: List[Dict[str, Any]]) -> str:
    high = [p for p in pairs if p["tier"] == "high"]
    borderline = [p for p in pairs if p["tier"] == "borderline"]

    lines: List[str] = [
        "# Suspected Duplicate Documents Report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Total documents in KB: {len(docs)}",
        f"High-confidence pairs found: {len(high)}",
        f"Borderline pairs found: {len(borderline)}",
        "",
        "---",
        "",
        "## HIGH CONFIDENCE (similarity >= 0.90)",
        "",
    ]

    if not high:
        lines += ["_No high-confidence duplicate pairs found._", ""]
    for i, pair in enumerate(high, 1):
        lines += [f"### Pair {i} (similarity: {pair['score']:.2f})", ""]
        lines += _format_doc_block("A", pair["a"])
        lines += _format_doc_block("B", pair["b"])
        lines += ["---", ""]

    lines += ["## BORDERLINE (0.70 <= similarity < 0.90)", ""]
    if not borderline:
        lines += ["_No borderline pairs found._", ""]
    for i, pair in enumerate(borderline, 1):
        lines += [f"### Pair {i} (similarity: {pair['score']:.2f})", ""]
        lines += _format_doc_block("A", pair["a"])
        lines += _format_doc_block("B", pair["b"])
        lines += ["---", ""]

    return "\n".join(lines).rstrip() + "\n"


async def _run() -> int:
    _safe_print("[INFO] Read-only diagnostic - no changes will be made to the KB")
    _safe_print("[INFO] Loading knowledge base chunks...")
    docs = await _load_documents()
    _safe_print(f"[INFO] Loaded {len(docs)} distinct KB documents")
    if len(docs) < 2:
        _safe_print("[WARN] Fewer than 2 documents - nothing to compare.")

    total_pairs = len(docs) * (len(docs) - 1) // 2
    _safe_print(f"[INFO] Comparing {total_pairs} document pairs...")
    pairs = _find_pairs(docs)
    high = sum(1 for p in pairs if p["tier"] == "high")
    borderline = sum(1 for p in pairs if p["tier"] == "borderline")

    report = _build_report(docs, pairs)
    out_path = Path(OUTPUT_FILENAME).resolve()
    out_path.write_text(report, encoding="utf-8")

    _safe_print(f"[DONE] High-confidence pairs: {high}")
    _safe_print(f"[DONE] Borderline pairs: {borderline}")
    _safe_print(f"[DONE] Report written to: {out_path}")
    return 0


def main() -> None:
    try:
        rc = asyncio.run(_main_async())
    except KeyboardInterrupt:
        _safe_print("\n[yellow]Interrupted.[/yellow]")
        rc = 130
    sys.exit(rc)


async def _main_async() -> int:
    try:
        return await _run()
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    main()
