"""
Unit tests for citation-based source filtering (citation_filter.filter_sources_by_citations).

Pure-function tests -- no DB, no reranker model, no Groq round-trip. Mirrors the
five cases from the Problem 4 task spec.

Usage:
    python tests/test_citation_filter.py
    # or: pytest tests/test_citation_filter.py -v
"""
import os
import sys

# Add python_backend/ to path so we can run standalone (file is under tests/unit/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.citation_filter import filter_sources_by_citations


def _names(chunks):
    return [c["filename"] for c in chunks]


def test_filename_match_only():
    # "[Source: Das Sobhan (2018)]" -> keep the Das/Sobhan file, drop Scour.
    answer = "Atterberg limits matter. [Source: Das Sobhan (2018)]"
    chunks = [
        {"filename": "Principles_of_geotech_2018_Das_Sobhan.pdf", "text": "soil basics"},
        {"filename": "Scour_in_cohesive_soil.pdf", "text": "scour content"},
    ]
    kept = filter_sources_by_citations(answer, chunks)
    assert _names(kept) == ["Principles_of_geotech_2018_Das_Sobhan.pdf"]


def test_author_year_in_chunk_match():
    # "[Source: Bishop (1955)]" matches the chunk that references Bishop (1955)
    # in its text, even though the filename doesn't mention Bishop.
    answer = "Slope stability follows classic work. [Source: Bishop (1955)]"
    chunks = [
        {"filename": "Landslide_PINN.pdf", "text": "...the method of slices as shown by Bishop (1955) remains foundational..."},
        {"filename": "Scour.pdf", "text": "...unrelated content about bridge scour..."},
    ]
    kept = filter_sources_by_citations(answer, chunks)
    assert _names(kept) == ["Landslide_PINN.pdf"]


def test_zero_matches_falls_back_to_all():
    # Cited author appears in neither filename nor text (and no matching year)
    # -> fall back to showing all chunks rather than losing attribution entirely.
    answer = "Bearing capacity theory. [Source: Meyerhof (1951)]"
    chunks = [
        {"filename": "Das_Sobhan.pdf", "text": "...no meyerhof here, just classification..."},
    ]
    kept = filter_sources_by_citations(answer, chunks)
    assert _names(kept) == ["Das_Sobhan.pdf"]


def test_no_source_block_falls_back_to_all():
    # No formal [Source: ...] block at all -> fall back to all chunks.
    answer = "Atterberg limits are important for classification."
    chunks = [
        {"filename": "A.pdf", "text": "a"},
        {"filename": "B.pdf", "text": "b"},
        {"filename": "C.pdf", "text": "c"},
    ]
    kept = filter_sources_by_citations(answer, chunks)
    assert len(kept) == 3


def test_multiple_citations_match_multiple_chunks():
    # Two citations match two of three chunks; the uncited one (Scour) is dropped.
    answer = "Per the literature. [Source: Das Sobhan (2018), Mitchell Soga (2005)]"
    chunks = [
        {"filename": "Principles_of_Geotechnical_Engineering_Das_Sobhan.pdf", "text": "x"},
        {"filename": "Fundamentals_of_Soil_Behavior_Mitchell_Soga.pdf", "text": "y"},
        {"filename": "Scour_in_cohesive_soil.pdf", "text": "z"},
    ]
    kept = filter_sources_by_citations(answer, chunks)
    assert _names(kept) == [
        "Principles_of_Geotechnical_Engineering_Das_Sobhan.pdf",
        "Fundamentals_of_Soil_Behavior_Mitchell_Soga.pdf",
    ]


if __name__ == "__main__":
    test_filename_match_only()
    test_author_year_in_chunk_match()
    test_zero_matches_falls_back_to_all()
    test_no_source_block_falls_back_to_all()
    test_multiple_citations_match_multiple_chunks()
    print("ALL_CITATION_FILTER_TESTS_PASSED")
