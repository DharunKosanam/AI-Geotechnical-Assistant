"""Calculator plugin interface for the Engineering Workspace.

A *calculator* is a self-contained deterministic test that GeoPilot can run
against an uploaded document. Each plugin declares:

  * ``id`` / ``name`` — stable identifier and human label.
  * ``trigger_phrases`` — lowercase strings that route a chat message to it
    (explicit intent matching only; NO auto-detection from document contents).
  * ``required_extension`` / ``required_label`` — the document kind it needs
    from the session pool (e.g. a ``.cpt`` sounding).
  * ``optional_params`` — parameters the user MAY supply inline in the message
    (e.g. groundwater level, unit weight); absent -> the calculator falls back
    to its own defaults.
  * ``compute`` — a PURE Python function that runs the deterministic
    calculation and returns a :class:`ComputeResult`. It never calls an LLM.
  * ``reference`` — the standard/method string quoted in the report.
  * ``interpret`` — an OPTIONAL async hook that turns the deterministic result
    into a plain-English AI draft (a separate, clearly-labelled section). This
    is the ONLY place an LLM is involved.

The registry (:mod:`app.workspace.calculators.registry`) holds the installed
calculators; the workspace chat route matches a message to one and runs it. The
underlying engineering math lives in the existing modules
(``cpt_interpretation``, ``layer_summary``) and is untouched — a plugin only
wires those functions behind this interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ParamSpec:
    """An optional inline parameter a calculator can parse from the message.

    ``aliases`` are the phrases a user might type for this parameter (matched
    case-insensitively, longest-first). ``key`` is the keyword the calculator's
    ``compute`` receives it under; ``unit`` is display-only.
    """

    key: str
    label: str
    unit: str
    aliases: Tuple[str, ...]


@dataclass
class ComputeResult:
    """Structured, deterministic output of a calculator run.

    This is the SINGLE SOURCE OF TRUTH for everything downstream: the on-screen
    result, the AI interpretation input, and the Excel export are all built from
    this object — never by re-parsing the original file or the AI text.

    STANDARD OUTPUT SCHEMA (drives the GENERIC Excel export)
    -------------------------------------------------------
    A calculator declares its exportable output as ``tables`` + ``summary``. The
    export builder is generic over this shape, so a NEW calculator gets the
    "Export to Excel" button for free just by populating these — with ZERO
    changes to routing, export, history or UI. Shape::

        tables  = [
            {
                "name": "CPT Data",                       # -> one worksheet
                "columns": [                              # header + number fmt
                    {"header": "Depth (m)", "format": "0.00"},
                    {"header": "Soil Behaviour Type", "format": None},  # text
                    ...
                ],
                "rows": [[0.20, ...], ...],               # aligned to columns
            },
            ...
        ]
        summary = {"Source file": "x.CPT", "Groundwater level (m)": 1.5, ...}

    Rules the builder enforces (never the calculator's concern): bold + frozen
    header row, numeric cells kept numeric, auto-sized columns, no merged cells,
    and NO AI/interpretation text in the file.
    """

    # Per-detected-layer summary (drives the ON-SCREEN results table only).
    layers: List[Dict[str, Any]] = field(default_factory=list)
    # Exportable tables in the standard schema above (drives the Excel sheets).
    tables: List[Dict[str, Any]] = field(default_factory=list)
    # Flat key/value metadata for the Excel "Summary" sheet.
    summary: Dict[str, Any] = field(default_factory=dict)
    # Run metadata (source file + params + method) for the chat reply / Q&A.
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Short deterministic result text shown at the top of the chat message.
    summary_text: str = ""
    # Opaque object handed to ``interpret`` (e.g. the CptInterpretationResult).
    raw: Any = None


@dataclass(frozen=True)
class Calculator:
    """A registered deterministic test plugin. See module docstring."""

    id: str
    name: str
    # One-line description handed to the LLM intent router so it can decide,
    # from natural phrasing, whether the user is asking to run this calculator.
    description: str
    trigger_phrases: Tuple[str, ...]
    reference: str
    required_extension: str
    required_label: str
    optional_params: Tuple[ParamSpec, ...]
    # Pure deterministic compute: (document_text, source_filename, params) -> result.
    compute: Callable[[str, str, Dict[str, Any]], ComputeResult]
    # Optional AI interpretation of the deterministic result (async, LLM-backed).
    interpret: Optional[Callable[[Any], Awaitable[Optional[Dict[str, Any]]]]] = None

    def trigger_hint(self) -> str:
        """The canonical trigger phrase to advertise to the user."""
        return self.trigger_phrases[0] if self.trigger_phrases else self.id
