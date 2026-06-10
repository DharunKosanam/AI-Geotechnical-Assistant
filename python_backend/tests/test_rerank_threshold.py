"""
Unit tests for the reranker score threshold (rag_service._apply_rerank_threshold).

Pure-function tests — no DB, no reranker model, no Groq round-trip. Mirrors the
three sanity cases from the threshold task.

Usage:
    python tests/test_rerank_threshold.py
    # or: pytest tests/test_rerank_threshold.py -v
"""
import os
import sys

# Add parent dir to path so we can run standalone (matches test_e2e.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.rag_service import _apply_rerank_threshold


def _chunks(scores):
    """Build minimal reranked chunk dicts, sorted desc as the pipeline does."""
    ranked = sorted(scores, reverse=True)
    return [
        {"rerank_score": float(s), "filename": f"doc{i}.pdf"}
        for i, s in enumerate(ranked)
    ]


def test_some_below_threshold_are_dropped():
    # [+5.5, +4.8, +4.2, +0.3, -1.8] -> 4 kept, 1 dropped
    chunks, no_high_conf = _apply_rerank_threshold(_chunks([5.5, 4.8, 4.2, 0.3, -1.8]))
    assert len(chunks) == 4
    assert no_high_conf is False
    assert all(c["low_confidence"] is False for c in chunks)


def test_all_below_threshold_set_low_confidence_flag():
    # [-1.0, -2.0, -3.0] -> empty displayed list, no_high_confidence flag set
    chunks, no_high_conf = _apply_rerank_threshold(_chunks([-1.0, -2.0, -3.0]))
    assert no_high_conf is True
    assert all(c["low_confidence"] is True for c in chunks)        # context only
    assert [c for c in chunks if not c["low_confidence"]] == []    # nothing displayed


def test_all_above_threshold_all_kept():
    # [+1.0, +2.0, +3.0, +4.0, +5.0] -> 5 kept, 0 dropped
    chunks, no_high_conf = _apply_rerank_threshold(_chunks([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert len(chunks) == 5
    assert no_high_conf is False
    assert all(c["low_confidence"] is False for c in chunks)


if __name__ == "__main__":
    test_some_below_threshold_are_dropped()
    test_all_below_threshold_set_low_confidence_flag()
    test_all_above_threshold_all_kept()
    print("ALL_RERANK_THRESHOLD_TESTS_PASSED")
