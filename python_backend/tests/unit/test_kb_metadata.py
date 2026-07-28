"""Phase 3: KB metadata extraction — pure parsing/coercion (no live LLM)."""
import pytest

from app.services import kb_metadata as m


def test_parse_json_from_prose_and_think_tags():
    raw = "<think>let me look</think> Sure: {\"title\": \"A\", \"year\": 2020} done"
    assert m._parse_json(raw) == {"title": "A", "year": 2020}
    assert m._parse_json("no json here") == {}
    assert m._parse_json("{bad json,,}") == {}


def test_coerce_normalises_fields():
    out = m._coerce(
        {"title": "  Soil Report ", "authors": "Lin", "year": "2023",
         "publication": "ASTM GTJ", "docType": "paper"},
        "file.pdf",
    )
    assert out["title"] == "Soil Report"
    assert out["authors"] == ["Lin"]          # scalar -> list
    assert out["year"] == 2023                  # str -> int
    assert out["publication"] == "ASTM GTJ"
    assert out["docType"] == "paper"
    assert out["extracted"] is True


def test_coerce_rejects_bad_values():
    out = m._coerce(
        {"title": "", "authors": None, "year": "not-a-year", "docType": "banana"},
        "kestrel-ridge-bh7.pdf",
    )
    assert out["title"]                         # falls back to filename-derived title
    assert out["authors"] == []
    assert out["year"] is None                  # unparseable -> None
    assert out["docType"] is None               # not in the allowed set -> None


def test_coerce_year_range_guard():
    assert m._coerce({"year": 12}, "f.pdf")["year"] is None      # out of range
    assert m._coerce({"year": 1998}, "f.pdf")["year"] == 1998


def test_fallback_shape():
    fb = m._fallback("Bolton-1986.pdf")
    assert fb["extracted"] is False
    assert fb["title"] and fb["authors"] == [] and fb["year"] is None


@pytest.mark.asyncio
async def test_extract_metadata_short_text_uses_fallback_no_llm():
    # Below the min length -> returns fallback without ever calling the LLM.
    out = await m.extract_metadata("   ", "report.pdf")
    assert out["extracted"] is False
    assert out["title"]
