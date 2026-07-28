"""KB upload metadata extraction (Phase 3).

Reads the first page or two of an upload and asks the configured LLM for
structured bibliographic metadata, used to PREFILL the student's metadata form
(they correct rather than type). Best-effort and provider-agnostic
(``get_llm().acomplete``): any failure — no API key, LLM error, unparseable
output — falls back to a filename-derived title with empty fields and never
raises. The canonical title is stored separately from the filename.
"""
import json
import re
from typing import Any, Dict

from app.services.rag_service import get_clean_title

# Allowed document types the model may choose from.
DOC_TYPES = frozenset(
    {"paper", "report", "thesis", "book", "standard", "slides", "dataset", "other"}
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON = re.compile(r"\{.*\}", re.DOTALL)

# ~2 pages is plenty for a title block / abstract; cap the prompt input.
_MAX_INPUT_CHARS = 4000

_PROMPT = """You are extracting bibliographic metadata from the FIRST PAGES of a document.

Return ONLY a single JSON object, no prose, with EXACTLY these keys:
  "title":       the document's title (string; "" if truly unknown)
  "authors":     array of author names (strings; [] if none found)
  "year":        publication year as an integer, or null
  "publication": journal / conference / publisher, or null
  "docType":     one of "paper","report","thesis","book","standard","slides","dataset","other"

Use null (or [] for authors) when a field is not present. Do not invent values.

TEXT:
{text}

JSON:"""


def _fallback(filename: str) -> Dict[str, Any]:
    return {
        "title": get_clean_title(filename or "")["title"],
        "authors": [],
        "year": None,
        "publication": None,
        "docType": None,
        "extracted": False,
    }


def _parse_json(raw: str) -> Dict[str, Any]:
    cleaned = _THINK.sub("", raw or "").strip()
    m = _JSON.search(cleaned)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _coerce(data: Dict[str, Any], filename: str) -> Dict[str, Any]:
    """Normalise the model's JSON into the stored shape, defensively."""
    title = str(data.get("title") or "").strip() or get_clean_title(filename or "")["title"]

    authors = data.get("authors") or []
    if not isinstance(authors, list):
        authors = [str(authors)]
    authors = [str(a).strip() for a in authors if str(a).strip()][:20]

    year = data.get("year")
    try:
        year = int(year) if year not in (None, "") else None
        if year is not None and not (1000 <= year <= 2100):
            year = None
    except (ValueError, TypeError):
        year = None

    publication = data.get("publication")
    publication = str(publication).strip() if publication else None

    doc_type = data.get("docType")
    doc_type = doc_type if doc_type in DOC_TYPES else None

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "publication": publication,
        "docType": doc_type,
        "extracted": True,
    }


async def extract_metadata(first_pages_text: str, filename: str) -> Dict[str, Any]:
    """Best-effort metadata for the prefill form. Never raises."""
    text = (first_pages_text or "").strip()
    if len(text) < 20:
        return _fallback(filename)
    try:
        from app.services.llm_service import get_llm

        llm = get_llm()
        resp = await llm.acomplete(_PROMPT.format(text=text[:_MAX_INPUT_CHARS]))
        data = _parse_json(str(resp))
        if not data:
            return _fallback(filename)
        return _coerce(data, filename)
    except Exception as e:  # no key, LLM/network error, etc. — prefill from filename
        print(f"[KB_META] metadata extraction failed, using filename fallback: {e}")
        return _fallback(filename)
