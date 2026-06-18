"""
Citation-based source filtering (Problem 4).

After the LLM answers, narrow the *displayed* sources to only the chunks the
model actually cited in its formal ``[Source: ...]`` block(s). Retrieval, rerank
and the prompt are untouched -- this is a pure post-processing pass over the
answer text and the already-retrieved chunks.

Pure functions only: no DB, no model, no network, no heavy imports. The matcher
is exposed as ``filter_sources_by_citations(answer_text, chunks)`` so it can be
unit-tested in isolation (see tests/test_citation_filter.py).
"""
import re
import sys
from typing import List, Dict


# Formal source block at/near the end of the answer, e.g.
#   [Source: Das Sobhan (2018), Mitchell Soga (2005)]
#   [Sources: Bishop (1955)]
# Case-insensitive; allows the singular or plural keyword. We deliberately only
# scan these bracketed blocks -- inline author mentions in the answer body are
# NOT treated as citations.
_SOURCE_BLOCK_RE = re.compile(r"\[sources?:\s*(.*?)\]", re.IGNORECASE | re.DOTALL)

# A 4-digit publication year (18xx-20xx covers the corpus comfortably).
_YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")

# Word runs used for tokenizing both filenames and citation entries. Splitting
# on this naturally drops underscores, hyphens and punctuation.
_WORD_RE = re.compile(r"[a-z0-9]+")

# Connector tokens to ignore when token-matching ("et al." etc.).
_STOPWORDS = {"et", "al", "and", "the", "of", "in", "a"}

# Proximity window (characters) for the author+year-in-chunk rule.
_PROXIMITY_CHARS = 200


def _safe_print(message: str) -> None:
    """print() that survives a cp1252 console when an author name is non-ASCII."""
    try:
        print(message)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(message.encode(enc, errors="backslashreplace").decode(enc))


def _split_top_level_commas(text: str) -> List[str]:
    """Split on commas that are NOT inside parentheses.

    Keeps "Abedi Koupai et al. (2020)" whole and only breaks between distinct
    citation entries like "... (2018), Mitchell Soga (2005)".
    """
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _extract_citation_entries(answer_text: str) -> List[str]:
    """Pull individual citation entries out of every [Source: ...] block."""
    entries: List[str] = []
    for block in _SOURCE_BLOCK_RE.findall(answer_text):
        entries.extend(_split_top_level_commas(block))
    return entries


def _filename_tokens(filename: str) -> set:
    """Normalize a filename to a set of lowercase tokens (extension stripped)."""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return set(_WORD_RE.findall(base.lower()))


def _citation_author_tokens(entry: str) -> set:
    """Author tokens of a citation entry: lowercase words minus year/stopwords."""
    toks = set(_WORD_RE.findall(entry.lower()))
    toks -= _STOPWORDS
    return {t for t in toks if not _YEAR_RE.fullmatch(t)}


def _entry_label(entry: str) -> str:
    """Order-preserving lowercase author label for logging, e.g. 'das sobhan'."""
    toks = [
        t for t in _WORD_RE.findall(entry.lower())
        if t not in _STOPWORDS and not _YEAR_RE.fullmatch(t)
    ]
    return " ".join(toks)


def _author_year_in_text(entry: str, text: str) -> bool:
    """True if an author surname and the cited year sit within _PROXIMITY_CHARS
    of each other in the chunk text.

    Handles citations like "Bishop (1955)" that refer to a paper referenced
    *inside* another chunk, where the filename won't match.
    """
    year_match = _YEAR_RE.search(entry)
    if not year_match:
        return False
    year = year_match.group(0)
    authors = _citation_author_tokens(entry)
    if not authors:
        return False
    low = text.lower()
    for m in re.finditer(re.escape(year), low):
        start = max(0, m.start() - _PROXIMITY_CHARS)
        end = m.end() + _PROXIMITY_CHARS
        window = low[start:end]
        if any(a in window for a in authors):
            return True
    return False


def filter_sources_by_citations(
    answer_text: str, chunks: List[Dict]
) -> List[Dict]:
    """Return only the chunks the LLM cited in its [Source: ...] block(s).

    Matching, first rule to fire wins per (chunk, entry):
      1. Filename token subset -- citation author tokens are all present in the
         normalized filename tokens.
      2. Author+year proximity -- author surname and year appear close together
         in the chunk's text content.

    Fallbacks (better to over-show than lose all attribution):
      - No [Source: ...] block in the answer       -> return all chunks.
      - A block exists but nothing matches          -> return all chunks.
      - Empty input (Problem 2 no-sources path)     -> return as-is (no-op).

    The chunk dicts are returned unchanged, so any ``low_confidence`` flag from
    the rerank step passes straight through.
    """
    if not chunks:
        return chunks

    entries = _extract_citation_entries(answer_text)
    if not entries:
        _safe_print(
            f"[CITATION_FILTER] No [Source: ...] block in answer - "
            f"falling back to all {len(chunks)} sources"
        )
        return chunks

    cited: List[Dict] = []
    matched_entries: set = set()
    for chunk in chunks:
        fname_tokens = _filename_tokens(chunk.get("filename", ""))
        text = chunk.get("text", "") or ""
        chunk_cited = False
        for entry in entries:
            author_tokens = _citation_author_tokens(entry)
            if author_tokens and author_tokens.issubset(fname_tokens):
                chunk_cited = True
                matched_entries.add(entry)
                continue
            if _author_year_in_text(entry, text):
                chunk_cited = True
                matched_entries.add(entry)
        if chunk_cited:
            cited.append(chunk)

    if not cited:
        _safe_print(
            f"[CITATION_FILTER] No matches found - "
            f"falling back to all {len(chunks)} sources"
        )
        return chunks

    # Order-preserving, de-duplicated matched labels for the log line.
    seen: set = set()
    labels: List[str] = []
    for entry in entries:
        if entry in matched_entries and entry not in seen:
            seen.add(entry)
            labels.append(_entry_label(entry))
    _safe_print(
        f"[CITATION_FILTER] Kept {len(cited)} of {len(chunks)} sources "
        f"(matched: {', '.join(labels)})"
    )
    return cited
