"""
Unit tests for the combined-search Mongo filter (rag_service._combined_search_filter).

Pure-function test -- no DB, no embedding model, no Groq round-trip. Verifies the
Problem 5 scoping contract: KB is global, user uploads are scoped to USER_ID, and
the two compete in one search (single $or, no per-category limits/priority).

Usage:
    python tests/test_combined_search_filter.py
    # or: pytest tests/test_combined_search_filter.py -v
"""
import os
import sys

# Add python_backend/ to path so we can run standalone (file is under tests/unit/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.config import USER_ID
from app.services.rag_service import _combined_search_filter


def test_filter_is_a_single_or_over_both_categories():
    flt = _combined_search_filter(USER_ID)
    assert set(flt.keys()) == {"$or"}, "combined search must be a single $or clause"
    clauses = flt["$or"]
    assert len(clauses) == 2, "exactly two category clauses (KB + user uploads)"


def test_kb_clause_is_global_unscoped():
    clauses = _combined_search_filter(USER_ID)["$or"]
    kb = [c for c in clauses if c.get("category") == "knowledge_base"]
    assert len(kb) == 1
    # KB is shared across users -> must NOT carry a userId scope.
    assert "userId" not in kb[0], "knowledge_base must stay global (no userId)"


def test_user_upload_clause_is_scoped_to_current_user():
    clauses = _combined_search_filter(USER_ID)["$or"]
    up = [c for c in clauses if c.get("category") == "user_upload"]
    assert len(up) == 1
    # Uploads are private -> must be scoped to the current user's id.
    assert up[0].get("userId") == USER_ID, "user_upload must be scoped to USER_ID"


def test_no_user_id_yields_kb_only():
    # Offline/eval path: with no user_id the user_upload branch is dropped, so
    # only the global knowledge_base clause remains.
    clauses = _combined_search_filter(None)["$or"]
    assert clauses == [{"category": "knowledge_base"}]


if __name__ == "__main__":
    test_filter_is_a_single_or_over_both_categories()
    test_kb_clause_is_global_unscoped()
    test_user_upload_clause_is_scoped_to_current_user()
    test_no_user_id_yields_kb_only()
    print("ALL_COMBINED_SEARCH_FILTER_TESTS_PASSED")
