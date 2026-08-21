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

# Add python_backend/ to path so we can run standalone (file is under tests/unit/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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


# --- structured (v3-xlsx) threshold: KB-path allowance ------------------------
def _mixed(entries):
    """entries: list of (score, chunkingVersion). Pre-sorted desc as the
    pipeline guarantees."""
    entries = sorted(entries, key=lambda e: e[0], reverse=True)
    return [
        {"rerank_score": float(s), "chunkingVersion": cv, "filename": f"doc{i}"}
        for i, (s, cv) in enumerate(entries)
    ]


def test_structured_threshold_none_is_byte_identical():
    # Explicit None (thread callers) == the pre-parameter behaviour.
    a, na = _apply_rerank_threshold(_mixed([(2.0, "v2"), (-3.6, "v3-xlsx")]))
    b, nb = _apply_rerank_threshold(
        _mixed([(2.0, "v2"), (-3.6, "v3-xlsx")]), structured_threshold=None)
    assert [c["rerank_score"] for c in a] == [c["rerank_score"] for c in b]
    assert na == nb


def test_structured_threshold_equal_to_flat_changes_nothing():
    # The config DEFAULT: structured == flat (0.0) -> identical outcome.
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(2.0, "v2"), (-3.6, "v3-xlsx")]), structured_threshold=0.0)
    assert [c["rerank_score"] for c in chunks] == [2.0]
    assert no_high is False


def test_structured_threshold_rescues_table_chunks_only():
    # The lab-inventory shape: prose chunk clears 0.0; v3-xlsx chunks score
    # -3.6/-4.3; a prose chunk at -1.8 must STILL be dropped (allowance is
    # structured-only).
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(2.0, "v2"), (-1.8, "v2"), (-3.6, "v3-xlsx"), (-4.3, "v3-xlsx")]),
        structured_threshold=-11.0)
    kept = [(c["rerank_score"], c["chunkingVersion"]) for c in chunks]
    assert kept == [(2.0, "v2"), (-3.6, "v3-xlsx"), (-4.3, "v3-xlsx")]
    assert no_high is False
    assert all(c["low_confidence"] is False for c in chunks)


def test_all_structured_below_flat_survive_with_allowance():
    # Today's failure case: ONLY table chunks, all under 0.0 -> with the
    # allowance they are high-confidence, so no fall-through to GENERAL.
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(-3.6, "v3-xlsx"), (-4.3, "v3-xlsx")]), structured_threshold=-11.0)
    assert len(chunks) == 2 and no_high is False


def test_structured_allowance_has_its_own_floor():
    # A structured chunk at the reranker's floor is still noise.
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(-12.5, "v3-xlsx")]), structured_threshold=-11.0)
    assert no_high is True
    assert all(c["low_confidence"] is True for c in chunks)
