"""Fetched-HTML -> clean text for KB web ingestion (WEB_INGEST_ENABLED).

Turns one fetched page into the same shape every other KB format produces: a
single markdown-ish text unit the v2 chunker already knows how to split
(headings become ``#``-prefixed lines, which the chunker's section detection
keys on). Funding pages are mostly headings, eligibility bullets and tables,
so the extractor's job is: keep that structure, drop the chrome.

* Navigation, headers, footers, asides, cookie banners, breadcrumbs, forms
  and script/style/template content are removed BEFORE rendering — a page
  must not ingest as mostly menu text.
* Content is rendered from the page's <main> (or role=main / #content /
  <article>) when present, falling back to <body>.
* Tables are rendered row-per-line by the SAME renderer the v3-xlsx fix
  introduced (``file_processing._render_sheet_rows``) — header restated per
  block, one row per line — not reimplemented.
* The <title> is captured for ``canonicalTitle``.
* The extracted-to-raw ratio is reported; a very low ratio (JS-rendered
  shell) produces a WARNING, never a silent empty document. JavaScript-only
  pages are a documented limitation — there is deliberately no headless
  browser in this project.

Uses lxml.html (already installed). NOTE: lxml's ``Cleaner`` lives in the
separate ``lxml_html_clean`` package since lxml 5, which is NOT installed —
stripping is done manually here, on purpose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

import lxml.html

from app.services.file_processing import _render_sheet_rows

# Tags whose entire subtree is never content.
_STRIP_TAGS = frozenset({
    "script", "style", "noscript", "template", "iframe", "svg", "canvas",
    "form", "button", "input", "select", "textarea", "label",
    "nav", "header", "footer", "aside", "dialog",
})
# ARIA landmark roles that mark chrome regardless of tag.
_STRIP_ROLES = frozenset({"navigation", "banner", "contentinfo", "search", "dialog"})
# id/class substrings that mark chrome (checked against the joined id+class,
# lowercased). Deliberately short and conservative — over-stripping loses
# content silently, which is worse than a stray menu line.
_STRIP_ID_CLASS_TOKENS = ("cookie", "breadcrumb", "skip-link", "skiplink",
                          "back-to-top", "site-search")

_HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_LIST_TAGS = frozenset({"ul", "ol"})

# Below this extracted/raw ratio the page is probably a JS-rendered shell.
LOW_TEXT_RATIO = 0.01


@dataclass
class ExtractResult:
    title: str
    text: str
    char_count: int
    html_chars: int
    text_ratio: float
    warnings: List[str] = field(default_factory=list)
    headings: int = 0
    tables: int = 0
    list_items: int = 0


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _is_element(node: Any) -> bool:
    return isinstance(getattr(node, "tag", None), str)


def _is_chrome(el) -> bool:
    if el.tag in _STRIP_TAGS:
        return True
    if (el.get("role") or "").strip().lower() in _STRIP_ROLES:
        return True
    if (el.get("aria-hidden") or "").strip().lower() == "true":
        return True
    ident = ((el.get("id") or "") + " " + (el.get("class") or "")).lower()
    return any(tok in ident for tok in _STRIP_ID_CLASS_TOKENS)


def _strip_chrome(root) -> None:
    doomed = [el for el in root.iter() if _is_element(el) and el is not root and _is_chrome(el)]
    for el in doomed:
        parent = el.getparent()
        if parent is not None:  # may already be gone inside a dropped subtree
            el.drop_tree()      # keeps the tail text, drops the subtree


def _find_content_root(doc):
    for finder in (
        lambda d: d.find(".//main"),
        lambda d: next(iter(d.xpath("//*[@role='main']")), None),
        lambda d: next(iter(d.xpath("//*[@id='content' or @id='main-content']")), None),
        lambda d: d.find(".//article"),
    ):
        el = finder(doc)
        if el is not None:
            return el
    body = doc.find(".//body")
    return body if body is not None else doc


class _Renderer:
    """Walks the cleaned tree emitting text blocks. Direct text in containers
    accumulates into a paragraph buffer that flushes at block boundaries, so
    bare text inside <div>s is kept."""

    def __init__(self) -> None:
        self.blocks: List[str] = []
        self._para: List[str] = []
        self.headings = 0
        self.tables = 0
        self.list_items = 0

    # -- buffer handling ----------------------------------------------------
    def _push_inline(self, s: Optional[str]) -> None:
        s = _collapse(s or "")
        if s:
            self._para.append(s)

    def _flush(self) -> None:
        if self._para:
            self.blocks.append(" ".join(self._para))
            self._para = []

    def _emit(self, block: str) -> None:
        self._flush()
        block = block.rstrip()
        if block:
            self.blocks.append(block)

    # -- inline text of one element (whole subtree) -------------------------
    @staticmethod
    def _inline(el) -> str:
        return _collapse(el.text_content())

    # -- structures ---------------------------------------------------------
    def _render_list(self, el, depth: int = 0) -> None:
        self._flush()
        ordered = el.tag == "ol"
        n = 0
        for li in el:
            if not _is_element(li) or li.tag != "li":
                continue
            n += 1
            nested = [c for c in li if _is_element(c) and c.tag in _LIST_TAGS]
            own_text_parts = []
            if li.text:
                own_text_parts.append(li.text)
            for c in li:
                if _is_element(c) and c.tag in _LIST_TAGS:
                    if c.tail:
                        own_text_parts.append(c.tail)
                    continue
                if _is_element(c):
                    own_text_parts.append(c.text_content())
                if getattr(c, "tail", None):
                    own_text_parts.append(c.tail)
            item = _collapse(" ".join(p for p in own_text_parts if p))
            marker = f"{n}." if ordered else "-"
            if item:
                self.blocks.append("  " * depth + f"{marker} {item}")
                self.list_items += 1
            for c in nested:
                self._render_list(c, depth + 1)

    def _render_table(self, el) -> None:
        self._flush()
        rows: List[List[str]] = []
        for tr in el.iter("tr"):
            cells = [self._inline(cell) for cell in tr if _is_element(cell) and cell.tag in ("th", "td")]
            if any(cells):
                rows.append(cells)
        if not rows:
            return
        self.tables += 1
        caption_el = el.find("caption")
        caption = self._inline(caption_el) if caption_el is not None else ""
        name = caption or f"Table {self.tables}"
        text, _meta = _render_sheet_rows(name, rows, kind="Table")
        if text:
            self.blocks.append(text)

    # -- main walk ----------------------------------------------------------
    def render(self, el) -> None:
        self._push_inline(el.text)
        for child in el:
            if not _is_element(child):
                self._push_inline(getattr(child, "tail", None))
                continue
            tag = child.tag
            if tag in _HEADING_LEVEL:
                text = self._inline(child)
                if text:
                    self._emit("#" * _HEADING_LEVEL[tag] + " " + text)
                    self.headings += 1
            elif tag == "p" or tag == "blockquote":
                text = self._inline(child)
                if text:
                    self._emit(("> " if tag == "blockquote" else "") + text)
            elif tag in _LIST_TAGS:
                self._render_list(child)
            elif tag == "table":
                self._render_table(child)
            elif tag == "pre":
                self._emit(child.text_content().rstrip())
            elif tag == "dl":
                self._flush()
                term = ""
                for item in child:
                    if not _is_element(item):
                        continue
                    if item.tag == "dt":
                        term = self._inline(item)
                    elif item.tag == "dd":
                        d = self._inline(item)
                        if term or d:
                            self.blocks.append(f"{term}: {d}" if term else d)
            elif tag == "br":
                self._flush()
            elif tag == "hr":
                self._flush()
            else:
                # Generic container OR inline element: recurse; its text joins
                # the current paragraph unless a block child flushes it.
                self.render(child)
            self._push_inline(getattr(child, "tail", None))

    def finish(self) -> str:
        self._flush()
        return "\n\n".join(b for b in self.blocks if b.strip())


def extract_web_page(html: str, url: str = "") -> ExtractResult:
    """Extract one fetched HTML page. Never raises on messy markup; an empty
    or shell page comes back with warnings, not an exception."""
    html_chars = len(html or "")
    try:
        doc = lxml.html.fromstring(html)
    except Exception:
        return ExtractResult(title="", text="", char_count=0, html_chars=html_chars,
                             text_ratio=0.0,
                             warnings=["The page could not be parsed as HTML."])

    title = _collapse(doc.findtext(".//title") or "")

    root = _find_content_root(doc)
    _strip_chrome(root)
    r = _Renderer()
    r.render(root)
    text = r.finish()

    ratio = (len(text) / html_chars) if html_chars else 0.0
    warnings: List[str] = []
    if not text:
        warnings.append("No text could be extracted from this page.")
    elif ratio < LOW_TEXT_RATIO:
        warnings.append(
            f"Only {len(text)} characters were extracted from {html_chars:,} bytes of HTML "
            f"({ratio:.2%}). This page may build its content with JavaScript, which is not "
            f"supported — the stored document may be incomplete. Verify the preview carefully."
        )
    return ExtractResult(
        title=title, text=text, char_count=len(text), html_chars=html_chars,
        text_ratio=round(ratio, 4), warnings=warnings,
        headings=r.headings, tables=r.tables, list_items=r.list_items,
    )
