"""Phase 3: KB upload validation checks + dedup + relevance + concurrency."""
import pytest

from app.core import config
from app.services import kb_validation as v


# --- filename sanitisation ----------------------------------------------------
def test_sanitize_strips_paths_and_unsafe():
    assert v.sanitize_filename("../../etc/passwd") == "passwd"
    assert v.sanitize_filename("weird*name?.pdf") == "weird_name_.pdf"
    assert v.sanitize_filename("  ") == "upload"
    assert v.sanitize_filename("a/b/c.docx") == "c.docx"
    assert len(v.sanitize_filename("x" * 500)) <= 200


# --- content hash -------------------------------------------------------------
def test_hash_ignores_whitespace_and_case():
    a = [(1, "Hello   World\n\nfoo", False)]
    b = [(1, "hello world foo", False)]
    c = [(1, "hello world bar", False)]
    assert v.normalized_text_hash(a) == v.normalized_text_hash(b)
    assert v.normalized_text_hash(a) != v.normalized_text_hash(c)


# --- extraction quality gate --------------------------------------------------
def test_extraction_quality_accepts_good_doc():
    pages = [(i, "x" * 1000, False) for i in range(1, 4)]  # 3000 chars / 3 pages
    ok, reason = v.extraction_quality(pages)
    assert ok is True and reason == ""


def test_extraction_quality_rejects_too_little_content():
    ok, reason = v.extraction_quality([(1, "short", False)])
    assert ok is False and "characters extracted" in reason


def test_extraction_quality_rejects_low_chars_per_page():
    # 300 chars total (>= min content 200) but spread over 30 pages = 10 cpp
    pages = [(i, "x" * 10, False) for i in range(1, 31)]
    ok, reason = v.extraction_quality(pages)
    assert ok is False and "per page" in reason


# --- caps ---------------------------------------------------------------------
def test_size_and_page_caps():
    assert v.check_size(0)[0] is False
    assert v.check_size(config.KB_MAX_UPLOAD_BYTES + 1)[0] is False
    assert v.check_size(1000)[0] is True
    assert v.check_pages(config.KB_MAX_PAGES + 1)[0] is False
    assert v.check_pages(10)[0] is True


# --- PII scan -----------------------------------------------------------------
def test_scan_pii_finds_and_is_clean():
    found = v.scan_pii("Reach me at jane.doe@uvic.ca or 250-555-1234, id V00891234.")
    assert "jane.doe@uvic.ca" in found["emails"]
    assert found["phones"]
    assert "V00891234" in found["student_numbers"]
    assert v.scan_pii("Purely technical content about soil consolidation.") == {}


def test_pii_emails_detected_but_non_gating():
    found = v.scan_pii("Corresponding author: a.smith@university.edu")
    assert found.get("emails")               # still detected
    assert v.sensitive_pii(found) == {}       # but does NOT gate (public contact)


def test_pii_bare_numbers_not_flagged_as_ids():
    # DOI / grant / ISBN-style bare numbers must NOT be treated as student IDs.
    found = v.scan_pii("See https://doi.org/10.1016/12345678 under grant 87654321.")
    assert "student_numbers" not in found
    assert v.sensitive_pii(found) == {}


def test_pii_context_gated_ids_flagged():
    found = v.scan_pii("Student Number: 12345678 recorded. Applicant SIN 123456789.")
    assert "12345678" in found["student_numbers"]
    assert "123456789" in found["student_numbers"]
    assert v.sensitive_pii(found).get("student_numbers")   # gates


def test_sensitive_pii_filters_to_gating_kinds():
    s = v.sensitive_pii({"emails": ["a@b.com"], "phones": ["250-555-1234"], "student_numbers": ["V00891234"]})
    assert set(s.keys()) == {"phones", "student_numbers"}


# --- language -----------------------------------------------------------------
def test_detect_non_english():
    assert v.detect_non_english("这是一份关于土力学的中文文档，讨论固结沉降。")[0] is True
    assert v.detect_non_english(
        "This report describes the consolidation settlement of a soft clay deposit "
        "beneath a shallow foundation and the effective stress analysis performed."
    )[0] is False


# --- cosine / relevance -------------------------------------------------------
def test_cosine_and_relevance():
    assert v.cosine([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert v.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    centroid = [1.0, 0.0]
    c, outlier = v.relevance([1.0, 0.0], centroid)
    assert c == pytest.approx(1.0) and outlier is False
    c2, outlier2 = v.relevance([0.1, 1.0], centroid)  # cosine ~0.1 << 0.62
    assert outlier2 is True


# --- concurrency guard --------------------------------------------------------
def test_reserve_hash_blocks_concurrent_same_content():
    h = "deadbeef"
    v.release_hash(h)
    assert v.reserve_hash(h) is True
    assert v.reserve_hash(h) is False  # already in flight
    v.release_hash(h)
    assert v.reserve_hash(h) is True
    v.release_hash(h)


# --- async: centroid + dedup (fake collection, deterministic, DB-free) --------
class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeFiles:
    def __init__(self, first_chunks, exact=None):
        self._first_chunks = first_chunks
        self._exact = exact

    def find(self, flt, projection=None):
        return _FakeCursor(self._first_chunks)

    async def find_one(self, flt, projection=None):
        return self._exact


@pytest.mark.asyncio
async def test_kb_centroid_is_mean_and_cached(monkeypatch):
    chunks = [{"embedding": [1.0, 0.0, 0.0]}, {"embedding": [0.0, 1.0, 0.0]}]
    monkeypatch.setattr(v, "files_collection", _FakeFiles(chunks))
    v._centroid_cache = None
    cen = await v.get_kb_centroid(force=True)
    assert cen == [0.5, 0.5, 0.0]
    assert await v.get_kb_centroid() is cen  # cached
    v._centroid_cache = None


@pytest.mark.asyncio
async def test_dedup_embedding_soft_warn(monkeypatch):
    chunks = [{"embedding": [1.0, 0.0, 0.0], "filename": "a", "canonicalTitle": "A"},
              {"embedding": [0.0, 1.0, 0.0], "filename": "b", "canonicalTitle": "B"}]
    monkeypatch.setattr(v, "files_collection", _FakeFiles(chunks, exact=None))
    res = await v.check_duplicate("nohash", [1.0, 0.0, 0.0])
    assert res.exact_match is None
    assert res.near_cosine == pytest.approx(1.0)      # identical to chunk 'a'
    assert res.near_match["filename"] == "a"           # >= 0.95 warn threshold


@pytest.mark.asyncio
async def test_dedup_exact_hash_hard_match(monkeypatch):
    exact = {"filename": "dup.pdf", "canonicalTitle": "Dup", "batchId": "b1"}
    chunks = [{"embedding": [0.2, 0.9, 0.1], "filename": "x"}]
    monkeypatch.setattr(v, "files_collection", _FakeFiles(chunks, exact=exact))
    res = await v.check_duplicate("samehash", [1.0, 0.0, 0.0])
    assert res.exact_match == exact
    assert res.near_match is None  # cosine([1,0,0],[0.2,0.9,0.1]) ~0.21 < 0.95
