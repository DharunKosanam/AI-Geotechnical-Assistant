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


# --- structured threshold: content-based table detection (KB-path allowance) --
from app.services.rag_service import _is_table_like

# Realistic stand-ins mirroring the measured corpus: the v3-xlsx renderer's
# pipe rows, a table-heavy PDF's whitespace-columned numeric rows, and prose.
PIPE_TABLE = "## Sheet: Readings\n" + "\n".join(
    f"| PZ-{i} | 6.0 | 3.42 | 3.61 | 3.88 |" for i in range(1, 7))
COL_TABLE = "Depth (m)   qc (MPa)   fs (kPa)   u2 (kPa)\n" + "\n".join(
    f"{i * 0.5:.1f}         {1.3 + i:.2f}       {40 + i}         {100 + i}"
    for i in range(8))
PROSE = ("The computed factor of safety for the west slope is 1.42 for the "
         "long-term drained case and 1.61 for the short-term undrained case. "
         "The critical surface is a deep circle daylighting beyond the toe.")


def test_is_table_like_detection():
    ok, why = _is_table_like(PIPE_TABLE)
    assert ok and "pipe" in why
    ok, why = _is_table_like(COL_TABLE)
    assert ok and "columned" in why
    assert _is_table_like(PROSE) == (False, "")
    assert _is_table_like("") == (False, "")
    # A couple of numeric lines inside prose must NOT flip the verdict.
    mixed = PROSE + "\n1.5   1.30   41   48\n2.0   1.28   44   85\n" + PROSE
    assert _is_table_like(mixed)[0] is False


def _mixed(entries):
    """entries: list of (score, text). Pre-sorted desc as the pipeline does."""
    entries = sorted(entries, key=lambda e: e[0], reverse=True)
    return [
        {"rerank_score": float(s), "text": t, "filename": f"doc{i}"}
        for i, (s, t) in enumerate(entries)
    ]


def test_structured_threshold_none_is_byte_identical():
    # Explicit None (thread callers) == the pre-parameter behaviour.
    a, na = _apply_rerank_threshold(_mixed([(2.0, PROSE), (-3.6, PIPE_TABLE)]))
    b, nb = _apply_rerank_threshold(
        _mixed([(2.0, PROSE), (-3.6, PIPE_TABLE)]), structured_threshold=None)
    assert [c["rerank_score"] for c in a] == [c["rerank_score"] for c in b]
    assert na == nb


def test_structured_threshold_equal_to_flat_changes_nothing():
    # The config DEFAULT: structured == flat (0.0) -> identical outcome.
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(2.0, PROSE), (-3.6, PIPE_TABLE)]), structured_threshold=0.0)
    assert [c["rerank_score"] for c in chunks] == [2.0]
    assert no_high is False


def test_structured_threshold_rescues_table_chunks_only():
    # Prose chunk clears 0.0; pipe-table AND pdf-column-table chunks score
    # negative and are rescued; a prose chunk at -1.8 must STILL be dropped.
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(2.0, PROSE), (-1.8, PROSE), (-2.1, COL_TABLE), (-3.6, PIPE_TABLE)]),
        structured_threshold=-11.0)
    kept = [c["rerank_score"] for c in chunks]
    assert kept == [2.0, -2.1, -3.6]
    assert no_high is False
    assert all(c["low_confidence"] is False for c in chunks)


def test_all_structured_below_flat_survive_with_allowance():
    # The lab-inventory failure: ONLY table chunks, all under 0.0 -> with the
    # allowance they are high-confidence, so no fall-through to GENERAL.
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(-3.6, PIPE_TABLE), (-4.3, PIPE_TABLE)]), structured_threshold=-11.0)
    assert len(chunks) == 2 and no_high is False


def test_structured_allowance_has_its_own_floor():
    # A table chunk at the reranker's floor is still noise.
    chunks, no_high = _apply_rerank_threshold(
        _mixed([(-12.5, PIPE_TABLE)]), structured_threshold=-11.0)
    assert no_high is True
    assert all(c["low_confidence"] is True for c in chunks)


# --- meta-phrasing strip (RERANK_STRIP_META_PHRASING; reranker input only) ----
from app.services.rag_service import _strip_meta_phrasing


def test_strip_meta_phrasing_scaffold_queries():
    cases = {
        "check the lab inventory file and tell what we have in the inventory":
            "lab inventory file what we have in the inventory",
        "Check the piezometer readings file and tell me what the levels are doing.":
            "piezometer readings file what the levels are doing",
        "Please can you look at the field visit notes and tell me what was agreed?":
            "field visit notes what was agreed",
    }
    for raw, want in cases.items():
        assert _strip_meta_phrasing(raw).lower() == want.lower()


def test_strip_meta_phrasing_leaves_content_alone():
    for q in (
        "What is the factor of safety of the west slope of the Sooke embankment?",
        "the borehole logs show artesian pressure below 12 m",  # 'show' is content
        "How much settlement has plate SP-2 recorded?",
    ):
        assert _strip_meta_phrasing(q) == q


def test_strip_meta_phrasing_never_returns_a_stub():
    # If stripping would leave fewer than 3 words, the original is kept.
    assert _strip_meta_phrasing("check the file") == "check the file"
    assert _strip_meta_phrasing("") == ""
