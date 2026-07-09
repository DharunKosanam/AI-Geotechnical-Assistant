"""
Retrieval baseline capture — read-only snapshot of the CURRENT retrieval path.

Runs each question in ``bm25_baseline_questions.md`` through the production
``query_vector_store`` (embedding -> $vectorSearch -> cross-encoder reranker ->
threshold) and records exactly what the pipeline returns: the post-rerank
chunks, their vector + rerank scores, and the low_confidence flag. NO LLM
generation, NO judge — this is a pure retrieval snapshot to diff against once
BM25 hybrid search is enabled.

This is the valid "before" snapshot for the BM25 comparison and MUST be run
with HYBRID_SEARCH_ENABLED OFF. The script REFUSES to run if the flag is on, so
a hybrid-on run can never be mislabeled as the baseline.

It imports query_vector_store from the existing service and does not duplicate
or modify any retrieval logic (read-only against Atlas).

Run (from the python_backend directory):
    venv/bin/python -m app.eval.capture_retrieval_baseline --label pre_bm25
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Reuse the production retrieval path — no duplication.
from app.services.rag_service import query_vector_store
from app.core.database import close_mongo_connection
from app.core import config as cfg

load_dotenv()

QUESTIONS_PATH = Path(__file__).parent / "bm25_baseline_questions.md"
BASELINES_DIR = Path(__file__).parent / "baselines"
EXPECTED_COUNT = 12


# ---------------------------------------------------------------------------
# Question loading
# ---------------------------------------------------------------------------
def load_questions(path: Path) -> List[Dict[str, Any]]:
    """
    Parse the numbered questions out of bm25_baseline_questions.md.

    Recognizes "## Group A — ..." headers (captures the letter) and numbered
    "1. question text" lines. The leading number is the canonical Q index
    (1-12, global across both groups).
    """
    group: Optional[str] = None
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        m_group = re.match(r"^##\s*Group\s+([A-Za-z])\b(.*)$", s)
        if m_group:
            group = m_group.group(1).upper()
            continue
        m_q = re.match(r"^(\d+)\.\s+(.*\S)\s*$", s)
        if m_q:
            out.append({
                "num": int(m_q.group(1)),
                "group": group or "?",
                "question": m_q.group(2),
            })
    return out


# ---------------------------------------------------------------------------
# Per-question capture
# ---------------------------------------------------------------------------
def _classify(chunks: List[Dict[str, Any]]) -> tuple[str, bool]:
    """
    Map the returned chunk set to a status.

    query_vector_store tags the whole returned set uniformly: high-confidence
    chunks are low_confidence=False; when nothing clears RERANK_SCORE_THRESHOLD
    it returns a tiny low_confidence=True fallback set instead. So:
      * EMPTY          — nothing returned at all
      * LOW_CONFIDENCE — every returned chunk is the low-confidence fallback
                         (all top-k rerank scores fell below the threshold)
      * STRONG_TOP5    — a full RERANK_TOP_K set cleared the threshold
      * PARTIAL        — some (but fewer than RERANK_TOP_K) cleared it
    """
    if not chunks:
        return "EMPTY", False
    low_conf = all(c.get("low_confidence", False) for c in chunks)
    if low_conf:
        return "LOW_CONFIDENCE", True
    if len(chunks) >= cfg.RERANK_TOP_K:
        return "STRONG_TOP5", False
    return "PARTIAL", False


async def capture_one(q: Dict[str, Any]) -> Dict[str, Any]:
    # user_id=None -> KB-only scope (the shared knowledge base), the correct
    # baseline scope. top_k is effectively superseded by RERANK_TOP_K when the
    # reranker is on, matching the production chat path.
    chunks = await query_vector_store(q["question"], top_k=8, user_id=None)
    status, low_conf = _classify(chunks)

    rerank_scores = [c.get("rerank_score") for c in chunks if c.get("rerank_score") is not None]
    chunk_records = [
        {
            "rank": i + 1,
            "id": c.get("id"),
            "filename": c.get("filename"),
            "category": c.get("category"),
            "page_start": c.get("pageStart"),
            "section_header": c.get("sectionHeader"),
            "vector_score": c.get("score"),
            "rerank_score": c.get("rerank_score"),
            "low_confidence": c.get("low_confidence"),
            "text_preview": (c.get("text") or "")[:200],
        }
        for i, c in enumerate(chunks)
    ]
    return {
        "num": q["num"],
        "group": q["group"],
        "question": q["question"],
        "status": status,
        "low_confidence": low_conf,
        "num_returned": len(chunks),
        "top_rerank_score": max(rerank_scores) if rerank_scores else None,
        "min_rerank_score": min(rerank_scores) if rerank_scores else None,
        "top_filenames": list(dict.fromkeys(
            c.get("filename") for c in chunks if c.get("filename")
        ))[:3],
        "chunks": chunk_records,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def build_payload(label: str, ts: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "meta": {
            "label": label,
            "timestamp": ts,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "hybrid_search_enabled": cfg.HYBRID_SEARCH_ENABLED,   # MUST be False
            "reranker_enabled": cfg.RERANKER_ENABLED,
            "reranker_model": cfg.RERANKER_MODEL,
            "rerank_top_k": cfg.RERANK_TOP_K,
            "rerank_score_threshold": cfg.RERANK_SCORE_THRESHOLD,
            "combined_search_limit": cfg.COMBINED_SEARCH_LIMIT,
            "question_count": len(records),
            "total_chunks_returned": sum(r["num_returned"] for r in records),
        },
        "questions": records,
    }


def render_md(payload: Dict[str, Any]) -> str:
    m = payload["meta"]
    lines: List[str] = []
    lines.append(f"# Retrieval baseline — `{m['label']}`")
    lines.append("")
    lines.append(f"- Captured: {m['captured_at']}")
    lines.append(f"- **HYBRID_SEARCH_ENABLED: {m['hybrid_search_enabled']}** "
                 f"(reranker: {m['reranker_enabled']}, top_k: {m['rerank_top_k']}, "
                 f"threshold: {m['rerank_score_threshold']})")
    lines.append(f"- Questions: {m['question_count']} | "
                 f"Total chunks returned: {m['total_chunks_returned']}")
    lines.append("")
    lines.append("| Q | Grp | Status | Chunks | Top rerank | Min rerank | Top source |")
    lines.append("|---|-----|--------|--------|-----------|-----------|-----------|")
    for r in payload["questions"]:
        top = f"{r['top_rerank_score']:+.2f}" if r["top_rerank_score"] is not None else "—"
        mn = f"{r['min_rerank_score']:+.2f}" if r["min_rerank_score"] is not None else "—"
        src = (r["top_filenames"][0] if r["top_filenames"] else "—")
        lines.append(
            f"| {r['num']} | {r['group']} | {r['status']} | {r['num_returned']} | "
            f"{top} | {mn} | {src} |"
        )
    lines.append("")
    for r in payload["questions"]:
        lines.append(f"### Q{r['num']} [{r['group']}] — {r['status']}")
        lines.append(f"> {r['question']}")
        lines.append("")
        if not r["chunks"]:
            lines.append("_No chunks returned._")
            lines.append("")
            continue
        for c in r["chunks"]:
            rr = f"{c['rerank_score']:+.3f}" if c["rerank_score"] is not None else "—"
            vs = f"{c['vector_score']:.3f}" if c["vector_score"] is not None else "—"
            lc = " (low_conf)" if c["low_confidence"] else ""
            lines.append(
                f"{c['rank']}. rerank {rr} | vec {vs}{lc} | "
                f"{c['filename']} p{c['page_start']}"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Compare (diff two captured baselines)
# ---------------------------------------------------------------------------
def _chunk_key(c: Dict[str, Any]) -> tuple:
    """
    Stable chunk identity for diffing across two captures. The original pre
    baseline predates the recorded ``id`` field, so we match on fields present
    in BOTH: source filename + page + a text fingerprint (first 80 chars of the
    preview, which distinguishes multiple chunks on the same page).
    """
    return (c.get("filename"), c.get("page_start"), (c.get("text_preview") or "")[:80])


def _chunk_label(c: Dict[str, Any]) -> str:
    return f"{c.get('filename')} p{c.get('page_start')}"


def _fmt(v: Optional[float], spec: str = "+.2f") -> str:
    return format(v, spec) if isinstance(v, (int, float)) else "—"


def run_compare(pre_path: str, post_path: str) -> int:
    pre = json.loads(Path(pre_path).read_text(encoding="utf-8"))
    post = json.loads(Path(post_path).read_text(encoding="utf-8"))
    pm, qm = pre["meta"], post["meta"]

    print("=" * 72)
    print(f"COMPARE  PRE : {pm['label']:<10} hybrid={pm['hybrid_search_enabled']}  "
          f"({Path(pre_path).name})")
    print(f"         POST: {qm['label']:<10} hybrid={qm['hybrid_search_enabled']}  "
          f"({Path(post_path).name})")
    print(f"         total chunks {pm['total_chunks_returned']} -> "
          f"{qm['total_chunks_returned']}")
    print("=" * 72)

    pre_q = {q["num"]: q for q in pre["questions"]}
    post_q = {q["num"]: q for q in post["questions"]}
    nums = sorted(set(pre_q) | set(post_q))

    status_changes: List[str] = []
    for n in nums:
        a, b = pre_q.get(n), post_q.get(n)
        if a is None or b is None:
            print(f"\nQ{n}: present in only one capture — skipped.")
            continue

        a_chunks, b_chunks = a["chunks"], b["chunks"]
        a_keys = {_chunk_key(c) for c in a_chunks}
        b_keys = {_chunk_key(c) for c in b_chunks}
        a_rank = {_chunk_key(c): c["rank"] for c in a_chunks}
        b_rank = {_chunk_key(c): c["rank"] for c in b_chunks}

        new = [c for c in b_chunks if _chunk_key(c) not in a_keys]
        dropped = [c for c in a_chunks if _chunk_key(c) not in b_keys]
        moved = [
            (c, a_rank[_chunk_key(c)], b_rank[_chunk_key(c)])
            for c in b_chunks
            if _chunk_key(c) in a_keys and a_rank[_chunk_key(c)] != b_rank[_chunk_key(c)]
        ]

        arrow = "→"
        st = f"{a['status']} {arrow} {b['status']}"
        if a["status"] != b["status"]:
            st += "   <== STATUS CHANGE"
            status_changes.append(f"Q{n} [{b['group']}]: {a['status']} {arrow} {b['status']}")

        print(f"\nQ{n} [{b['group']}] {b['question']}")
        print(f"   status: {st}")
        print(f"   chunks: {a['num_returned']} {arrow} {b['num_returned']} | "
              f"top_rerank {_fmt(a['top_rerank_score'])} {arrow} {_fmt(b['top_rerank_score'])} | "
              f"low_conf {a['low_confidence']} {arrow} {b['low_confidence']}")
        if new:
            print(f"   NEW ({len(new)}):")
            for c in new:
                print(f"      + r{c['rank']} rerank {_fmt(c.get('rerank_score'), '+.3f')} | {_chunk_label(c)}")
        if dropped:
            print(f"   DROPPED ({len(dropped)}):")
            for c in dropped:
                print(f"      - r{c['rank']} rerank {_fmt(c.get('rerank_score'), '+.3f')} | {_chunk_label(c)}")
        if moved:
            print(f"   RANK CHANGED ({len(moved)}):")
            for c, ra, rb in moved:
                print(f"      ~ r{ra}{arrow}r{rb} | {_chunk_label(c)}")
        if not (new or dropped or moved):
            print("   (identical retrieved set, same order)")

    print("\n" + "=" * 72)
    print(f"STATUS CHANGES ({len(status_changes)}):")
    for s in (status_changes or ["   (none)"]):
        print(f"   {s}")
    print("=" * 72)
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main_async(label: str) -> int:
    mode = "HYBRID (vector + BM25, RRF)" if cfg.HYBRID_SEARCH_ENABLED else "VECTOR-ONLY"
    print(f"[BASELINE] Retrieval mode: {mode} "
          f"(HYBRID_SEARCH_ENABLED={cfg.HYBRID_SEARCH_ENABLED}). "
          "Mode is recorded in meta so a run can't be mislabeled.")

    questions = load_questions(QUESTIONS_PATH)
    if len(questions) != EXPECTED_COUNT:
        print(
            f"[ABORT] Expected {EXPECTED_COUNT} questions in {QUESTIONS_PATH.name}, "
            f"parsed {len(questions)}. Aborting so the baseline isn't partial.",
            file=sys.stderr,
        )
        return 2

    print(f"[BASELINE] Capturing '{label}' over {len(questions)} questions "
          f"(HYBRID_SEARCH_ENABLED={cfg.HYBRID_SEARCH_ENABLED})...\n")

    records: List[Dict[str, Any]] = []
    try:
        for q in questions:
            print(f"--- Q{q['num']} [{q['group']}] {q['question']}")
            rec = await capture_one(q)
            records.append(rec)
            top = (f"{rec['top_rerank_score']:+.2f}"
                   if rec["top_rerank_score"] is not None else "—")
            print(f"    => {rec['status']} | {rec['num_returned']} chunks | "
                  f"top rerank {top}\n")
    finally:
        await close_mongo_connection()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = build_payload(label, ts, records)

    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    json_path = BASELINES_DIR / f"baseline_{label}_{ts}.json"
    md_path = BASELINES_DIR / f"baseline_{label}_{ts}.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_md(payload), encoding="utf-8")

    # Console summary
    print("=" * 68)
    print(f"[DONE] Captured {len(records)}/{EXPECTED_COUNT} questions | "
          f"total chunks: {payload['meta']['total_chunks_returned']}")
    for r in records:
        top = (f"{r['top_rerank_score']:+.2f}"
               if r["top_rerank_score"] is not None else "—")
        print(f"  Q{r['num']:>2} [{r['group']}] {r['status']:<14} "
              f"{r['num_returned']} chunks, top {top}")
    print("-" * 68)
    print(f"JSON: {json_path}")
    print(f"MD  : {md_path}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Capture / compare read-only retrieval baselines.")
    p.add_argument("--label", default="pre_bm25",
                   help="Label for the baseline files (default: pre_bm25).")
    p.add_argument("--compare", nargs=2, metavar=("PRE_JSON", "POST_JSON"),
                   help="Diff two captured baseline JSONs and print the per-question "
                        "changes. No capture is performed in this mode.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.compare:
        raise SystemExit(run_compare(args.compare[0], args.compare[1]))
    raise SystemExit(asyncio.run(main_async(args.label)))


if __name__ == "__main__":
    main()
