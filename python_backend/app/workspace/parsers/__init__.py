"""Parsers for the Engineering Workspace.

Two families live here:

* the CPT sounding text parser (``cpt``), consumed directly by the CPT
  calculator (unchanged), and
* the INSTRUMENT parsers (``odisi``, ``campbell``) that turn a raw logger /
  interrogator file into a structured numeric dataset (see ``base``), found by
  signature via ``registry.sniff`` and bound to calculators by
  ``dataset_kind``.

Registration order below is sniff precedence (most specific signature first).
"""

from app.workspace.parsers import registry
from app.workspace.parsers.base import (
    SNIFF_BYTES,
    Parser,
    ParserError,
    ParserResult,
)
from app.workspace.parsers.cpt import (
    CptRow,
    CptSounding,
    parse_cpt,
    parse_cpt_text,
)
from app.workspace.parsers.campbell import CAMPBELL_PARSER
from app.workspace.parsers.odisi import ODISI_PARSER

# Sniff precedence: ODiSI (tab key/value header) before Campbell (CSV header row).
registry.register(ODISI_PARSER)
registry.register(CAMPBELL_PARSER)

__all__ = [
    "CptRow",
    "CptSounding",
    "parse_cpt",
    "parse_cpt_text",
    "SNIFF_BYTES",
    "Parser",
    "ParserError",
    "ParserResult",
    "registry",
    "ODISI_PARSER",
    "CAMPBELL_PARSER",
]
