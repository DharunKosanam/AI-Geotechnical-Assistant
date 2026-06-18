"""
Unit tests for the index-time duplicate check (kb_admin._duplicate_preflight).

No DB and no real PDF: the fingerprint store is synthetic, PDF text extraction is
monkeypatched, and stdin is stubbed. Exercises the real decision logic, the real
token_set_ratio / build_fingerprint, the warning output, and both branches.

Usage:
    python tests/test_duplicate_preflight.py
    # or: pytest tests/test_duplicate_preflight.py -v
"""
import builtins
import io
import os
import sys
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.scripts import kb_admin
from app.scripts.kb_find_duplicates import build_fingerprint

# A realistic existing-document text (title line + abstract-ish body).
REF_TEXT = (
    "Influence of shearing strain rate on the mechanical behaviour of three "
    "structured clays\n"
    "This paper presents the stress-strain behaviour of three structured clays "
    "tested over a range of shearing strain rates in triaxial compression. The "
    "results show that strain rate has a measurable effect on undrained shear "
    "strength and stiffness, with implications for slope stability and bearing "
    "capacity analysis in natural clay deposits."
)

# Disjoint, clearly-different candidate (off-topic).
DIFFERENT_TEXT = (
    "Serotonin synthesis pathway in the human brain involves tryptophan "
    "hydroxylase converting tryptophan to 5-hydroxytryptophan, then aromatic "
    "amino acid decarboxylase producing serotonin in neurons."
)


def _kb_doc(text: str, filename: str) -> dict:
    fp = build_fingerprint(text, filename)
    return {
        "filename": filename,
        "title": fp["title"],
        "snippet": fp["snippet"],
        "chunk_count": 18,
        "tokens": fp["tokens"],
    }


KB_DOCS = [
    _kb_doc(REF_TEXT, "existing_clays_paper.pdf"),
    _kb_doc(
        "Bridge scour hydraulics around circular piers in cohesive soils.",
        "scour.pdf",
    ),
]


@contextmanager
def _patched(head_text: str, stdin_value: str):
    """Stub PDF extraction + stdin for one preflight call."""
    orig_extract = kb_admin._extract_pdf_head_text
    orig_input = builtins.input
    kb_admin._extract_pdf_head_text = lambda path, max_chars=1500: head_text
    builtins.input = lambda *a, **k: stdin_value
    try:
        yield
    finally:
        kb_admin._extract_pdf_head_text = orig_extract
        builtins.input = orig_input


def _run(head_text: str, stdin_value: str):
    """Run preflight, returning (proceed_bool, captured_stdout)."""
    buf = io.StringIO()
    with _patched(head_text, stdin_value), redirect_stdout(buf):
        proceed = kb_admin._duplicate_preflight(Path("candidate_new.pdf"), KB_DOCS)
    return proceed, buf.getvalue()


def test_different_pdf_no_warning():
    # stdin is 'no', but a non-duplicate must never even prompt -> still proceeds.
    proceed, out = _run(DIFFERENT_TEXT, "no")
    assert proceed is True
    assert "[OK] No duplicates found above threshold" in out
    assert "[WARN]" not in out


def test_duplicate_cancel_path():
    # Near-identical text -> warning fires; input != 'add' -> abort, no index.
    proceed, out = _run(REF_TEXT, "no")
    assert proceed is False
    assert "[WARN] Possible duplicate detected for: candidate_new.pdf" in out
    assert "existing_clays_paper.pdf" in out          # top match shown
    assert "[INFO] Cancelled by user" in out


def test_duplicate_proceed_path():
    # Near-identical text -> warning fires; input == 'add' -> proceed.
    proceed, out = _run(REF_TEXT, "add")
    assert proceed is True
    assert "[WARN] Possible duplicate detected" in out
    assert "[INFO] User confirmed - indexing despite duplicate warning." in out


if __name__ == "__main__":
    test_different_pdf_no_warning()
    test_duplicate_cancel_path()
    test_duplicate_proceed_path()
    print("ALL_DUPLICATE_PREFLIGHT_TESTS_PASSED")
