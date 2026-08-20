"""Instrument parser registry: ``register`` / ``get`` / ``sniff``.

Mirrors the calculator registry's explicit style: parsers are registered in a
fixed order (most specific signature first) and ``sniff`` returns the id of the
FIRST parser whose signature matches the leading bytes of a file, or ``None``.
``sniff`` is a pure function of at most :data:`SNIFF_BYTES` bytes -- it never
opens or reads a file, so the caller decides how much of the upload to peek at.
No match means "not an instrument file": the caller falls through to whatever
it did before (the plain document path), unchanged.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.workspace.parsers.base import SNIFF_BYTES, Parser

# Registration order == sniff precedence.
_PARSERS: List[Parser] = []
_BY_ID: Dict[str, Parser] = {}


def register(parser: Parser) -> Parser:
    """Install a parser (idempotent for the same id; re-registration replaces)."""
    if parser.id in _BY_ID:
        _PARSERS[:] = [p for p in _PARSERS if p.id != parser.id]
    _PARSERS.append(parser)
    _BY_ID[parser.id] = parser
    return parser


def get(parser_id: str) -> Optional[Parser]:
    """Look up a parser by id, or None."""
    return _BY_ID.get(parser_id)


def all_parsers() -> List[Parser]:
    """Installed parsers in registration (= sniff precedence) order."""
    return list(_PARSERS)


def sniff(head_bytes: bytes) -> Optional[str]:
    """Return the id of the first parser whose signature matches, else None.

    Only the first :data:`SNIFF_BYTES` bytes are consulted even if more are
    passed, so behaviour cannot depend on how much of the file the caller read.
    """
    if not head_bytes:
        return None
    head = bytes(head_bytes[:SNIFF_BYTES])
    for parser in _PARSERS:
        try:
            if parser.sniff(head):
                return parser.id
        except Exception:  # noqa: BLE001 - a broken signature must not break sniffing
            continue
    return None


def dataset_kinds() -> List[str]:
    """Distinct dataset kinds the installed parsers can produce."""
    kinds: List[str] = []
    for p in _PARSERS:
        if p.dataset_kind not in kinds:
            kinds.append(p.dataset_kind)
    return kinds
