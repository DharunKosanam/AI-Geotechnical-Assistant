"""Instrument parser contract for the Engineering Workspace.

A *parser* turns one instrument file (a Luna ODiSI strain export, a Campbell
Scientific pressure-cell log, ...) into a structured NUMERIC dataset. It is a
file-format transformation only: it makes no engineering claim, so it is safe
to run automatically on upload. Engineering results come from the sibling
``calculators`` package, which binds to a parser's ``dataset_kind`` (never to a
file extension or parser id) and runs only on an explicit user trigger.

Contract
--------
:class:`ParserResult` -- what every parser returns:

  * ``parser_id``     stable id of the parser that produced it (``"odisi_tsv"``)
  * ``dataset_kind``  the KIND of dataset (``"strain_distributed"``,
                      ``"pressure_timeseries"``); calculators bind to this
  * ``metadata``      dict of plain (JSON-serialisable) values: header fields
                      in ``snake_case``, derived facts (counts, ranges), and
                      the raw header lines under ``metadata["_raw_header"]``
  * ``arrays``        dict of numpy arrays -- the numeric payload. NEVER
                      stored in Mongo; persisted as a compressed ``.npz``
  * ``warnings``      list of human-readable strings for anything the parser
                      tolerated (length mismatches, ragged rows, header
                      contradictions). Parsers append here instead of raising
                      whenever the file is still usable

:class:`Parser` -- a registered parser: id, dataset kind, human label, a
``sniff(head_bytes) -> bool`` predicate over the FIRST 2 KB of a file, and a
``parse(path, progress=None) -> ParserResult`` function that streams the file
line by line (never reads it whole into memory as text).

``progress`` is an optional callback ``(fraction: float) -> None`` (0..1) so a
background job can report a percentage without the parser knowing about jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# How many leading bytes ``sniff`` may look at. A pure function of this prefix.
SNIFF_BYTES = 2048

ProgressCallback = Callable[[float], None]


class ParserError(ValueError):
    """The file is not a valid file for this parser at all (unrecoverable)."""


@dataclass
class ParserResult:
    """Structured numeric dataset produced by a parser. See module docstring."""

    parser_id: str
    dataset_kind: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    arrays: Dict[str, np.ndarray] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def shapes(self) -> Dict[str, Tuple[int, ...]]:
        """Array name -> shape (JSON-friendly summary for the pointer document)."""
        return {name: tuple(int(n) for n in arr.shape) for name, arr in self.arrays.items()}

    def dtypes(self) -> Dict[str, str]:
        return {name: str(arr.dtype) for name, arr in self.arrays.items()}


@dataclass(frozen=True)
class Parser:
    """A registered instrument parser."""

    id: str
    dataset_kind: str
    label: str  # short human label used in UI badges, e.g. "DFOS"
    # File extensions this parser is USUALLY seen with -- advisory only (the
    # upload picker's accept list); detection is by ``sniff``, never by name.
    extensions: Tuple[str, ...]
    # Pure predicate over the first SNIFF_BYTES of the file.
    sniff: Callable[[bytes], bool]
    # Streaming parse of a file on disk.
    parse: Callable[..., ParserResult]
