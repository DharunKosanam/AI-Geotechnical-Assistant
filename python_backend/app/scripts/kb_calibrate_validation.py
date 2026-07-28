"""Phase 3 calibration harness (measure-only; settles no thresholds).

Produces the score distributions the owner reviews before defaults are chosen:
  1. Extraction quality  -> chars-per-page across the real KB vs a synthetic scan
  2. Duplicate detection -> first-chunk cosine: distinct-doc band vs self/near-dup
  3. Relevance           -> doc-to-KB-centroid cosine vs synthetic off-topic docs

Read-only against Mongo; loads the embedding model to embed the synthetic
anchors. Run from python_backend:
    ./venv/bin/python -m app.scripts.kb_calibrate_validation
"""
import asyncio
import math
from typing import Dict, List

import fitz  # PyMuPDF

from app.core.database import files_collection
from app.services.rag_service import get_embedding_model, extract_pages_from_pdf_with_ocr

KB = {"category": "knowledge_base"}


def _pct(xs: List[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _stats(xs: List[float]) -> str:
    if not xs:
        return "(empty)"
    mean = sum(xs) / len(xs)
    # 3 decimals for cosine-scale values (< 10), 1 for large counts like chars/page.
    fmt = "{:.3f}" if max(abs(x) for x in xs) < 10 else "{:.1f}"
    f = fmt.format
    return (f"n={len(xs)} min={f(min(xs))} p1={f(_pct(xs,0.01))} p5={f(_pct(xs,0.05))} "
            f"p25={f(_pct(xs,0.25))} median={f(_pct(xs,0.5))} mean={f(mean)} "
            f"p95={f(_pct(xs,0.95))} max={f(max(xs))}")


def _normalize(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


async def extraction_quality() -> None:
    print("\n=== 1. EXTRACTION QUALITY (chars per page) ===")
    pipeline = [
        {"$match": KB},
        {"$group": {"_id": "$filename",
                    "chars": {"$sum": {"$strLenCP": {"$ifNull": ["$text", ""]}}},
                    "pages": {"$addToSet": "$pageStart"}}},
        {"$project": {"chars": 1, "npages": {"$size": "$pages"}}},
    ]
    cpp: List[float] = []
    async for d in files_collection.aggregate(pipeline):
        npages = max(1, d.get("npages", 1))
        cpp.append(d["chars"] / npages)
    print(f"KB good-doc chars/page:  {_stats(cpp)}")
    print(f"  -> lowest 5 docs chars/page: {[round(x) for x in sorted(cpp)[:5]]}")

    # Synthetic 'scan': an image-only page (grey rectangle, no text layer). The KB
    # PDF extractor will try OCR; a non-text image yields ~0 usable chars.
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(80, 80, 500, 700), fill=(0.6, 0.6, 0.6))
    scan_bytes = doc.tobytes()
    doc.close()
    triples = extract_pages_from_pdf_with_ocr(scan_bytes)
    chars = sum(len(t) for _, t, _ in triples)
    pages = len(triples) or 1
    print(f"synthetic image-only PDF: chars/page = {chars / pages:.1f}  (pages={len(triples)})")


async def _first_chunk_embeddings():
    embs: List[List[float]] = []
    fns: List[str] = []
    texts: Dict[str, str] = {}
    cur = files_collection.find(
        {**KB, "chunkIndex": 0},
        {"embedding": 1, "filename": 1, "text": 1},
    )
    async for d in cur:
        e = d.get("embedding")
        if e and d.get("filename"):
            embs.append(list(e))
            fns.append(d["filename"])
            texts[d["filename"]] = d.get("text", "")
    return embs, fns, texts


async def dedup(model) -> List[List[float]]:
    print("\n=== 2. DUPLICATE DETECTION (first-chunk cosine) ===")
    embs, fns, texts = await _first_chunk_embeddings()
    print(f"first-chunk embeddings loaded: {len(embs)} docs")
    norm = [_normalize(e) for e in embs]

    # Distinct-doc band: cosine between every pair of DIFFERENT docs.
    sims: List[float] = []
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            sims.append(_dot(norm[i], norm[j]))
    print(f"distinct-doc cosine:     {_stats(sims)}")
    print(f"  -> p99={_pct(sims,0.99):.3f}  p999={_pct(sims,0.999):.3f}  max={max(sims):.3f}")

    # Same-doc band: re-embed a doc's first chunk (true re-encode) -> ~1.0.
    self_sims: List[float] = []
    for fn in fns[:12]:
        v = list(model.embed([texts[fn]]))[0]
        v = _normalize(v.tolist() if hasattr(v, "tolist") else list(v))
        self_sims.append(_dot(v, norm[fns.index(fn)]))
    print(f"self re-embed cosine:    {_stats(self_sims)}   (true re-encode ~1.0)")

    # Near-dup band: minor edit (append a sentence, tweak a number).
    near: List[float] = []
    for fn in fns[:12]:
        edited = texts[fn].replace("the", "a", 3) + " (revised draft, additional note appended)."
        v = list(model.embed([edited]))[0]
        v = _normalize(v.tolist() if hasattr(v, "tolist") else list(v))
        near.append(_dot(v, norm[fns.index(fn)]))
    print(f"near-dup (minor edit):   {_stats(near)}")
    return embs


async def relevance(model, embs: List[List[float]]) -> None:
    print("\n=== 3. RELEVANCE vs KB CENTROID (cosine) ===")
    dim = len(embs[0])
    centroid = [0.0] * dim
    for e in embs:
        for k in range(dim):
            centroid[k] += e[k]
    centroid = _normalize([x / len(embs) for x in centroid])

    dists = [_dot(_normalize(e), centroid) for e in embs]
    mean = sum(dists) / len(dists)
    std = math.sqrt(sum((x - mean) ** 2 for x in dists) / len(dists))
    print(f"KB doc-to-centroid:      {_stats(dists)}")
    print(f"  mean={mean:.3f} std={std:.3f}  |  mean-1s={mean-std:.3f}  mean-2s={mean-2*std:.3f}  mean-3s={mean-3*std:.3f}")

    offtopic = {
        "cooking": "Preheat the oven to 200 degrees. Whisk the eggs with sugar and flour, "
                   "fold in the melted butter, and bake the sponge cake for twenty-five minutes.",
        "sports": "The striker dribbled past two defenders and curled the ball into the top "
                  "corner for a stunning goal in the final minute of the match.",
        "finance": "Quarterly earnings beat analyst expectations as revenue grew twelve percent "
                   "and the board approved a dividend increase and a share buyback program.",
    }
    for name, txt in offtopic.items():
        v = list(model.embed([txt]))[0]
        v = _normalize(v.tolist() if hasattr(v, "tolist") else list(v))
        print(f"  off-topic[{name:8s}] -> centroid cosine = {_dot(v, centroid):.3f}")


async def main() -> None:
    model = get_embedding_model()
    await extraction_quality()
    embs = await dedup(model)
    await relevance(model, embs)
    print("\n[calibration complete — no thresholds settled]")


if __name__ == "__main__":
    asyncio.run(main())
