"""CPT sounding parser (CPTLOG format).

Reads a real Cone Penetration Test (CPT) sounding in the field ``CPTLOG``
key=value format and exposes it as a :class:`CptSounding`. The file is parsed
LINE BY LINE (it is NOT CSV):

    $                                  <- start marker (ignored)
    HA=1,HB=2,HC=CPTLOG-1.00,...,MA=0.580,...   <- header: comma-separated
                                                   KEY=value pairs -> header dict
    #                                  <- data-start marker (ignored)
    D=0.000,QC=2.690,FS=50,U=165,TA=4.7,B=0     <- one reading per line
    D=0.020,QC=2.710,FS=51,U=166,...
    ...

Per-reading mapping (only these four are required; any other key such as TA, B,
NA, NB, NC is optional and simply ignored):
    D  -> z    depth below ground surface   [m]
    QC -> qc   measured cone resistance      [MPa]
    FS -> fs   sleeve friction               [kPa]
    U  -> u2   pore pressure behind cone (u2)[kPa]

The cone net area ratio comes from the header key ``MA`` (``area_ratio_source``
= "MA"); it falls back to :data:`DEFAULT_AREA_RATIO` only when ``MA`` is absent.

Output contract is UNCHANGED from the previous CSV parser -- ``CptSounding``
with a ``.header`` dict, depth-ordered ``.rows`` of ``CptRow(z, qc, fs, u2)``,
``.area_ratio`` and ``.area_ratio_source`` -- so nothing downstream changes.
This module ONLY reads and structures the raw sounding; every engineering
quantity is derived downstream by
``app.workspace.calculators.cpt_interpretation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

# Cone net area ratio assumed when the sounding header declares no MA. 0.80 is
# the typical value for a standard piezocone; the parser records whether the
# value was read from the file (``area_ratio_source`` == "MA") or defaulted so
# downstream code and the interpretation can be honest about provenance.
DEFAULT_AREA_RATIO = 0.80

# Depth to groundwater (m) assumed when the header omits it. CPTLOG headers do
# not carry a water table, so in practice the engineer supplies it via the
# route's ``groundwater_level`` override; this default only keeps the pipeline
# runnable.
DEFAULT_GWL = 0.0

# Reading fields we map onto CptRow. Everything else on a data line is optional
# metadata (TA, B, NA, NB, NC, ...) and ignored.
_START_MARKER = "$"
_DATA_MARKER = "#"


@dataclass(frozen=True)
class CptRow:
    """A single CPT reading at one depth."""

    z: float   # depth [m]
    qc: float  # measured cone resistance [MPa]
    fs: float  # sleeve friction [kPa]
    u2: float  # pore pressure measured behind the cone tip (u2) [kPa]


@dataclass
class CptSounding:
    """A parsed CPT sounding: metadata header plus depth-ordered readings."""

    header: Dict[str, str]
    rows: List[CptRow]
    area_ratio: float = DEFAULT_AREA_RATIO
    # "MA" if area_ratio came from the file header, "default" if we assumed it.
    area_ratio_source: str = "default"

    @property
    def gwl(self) -> float:
        """Groundwater level (depth to water table, m) from the header.

        CPTLOG files do not standardise a water-table key, so this is normally
        the default and the value is supplied by the caller (the route's
        ``groundwater_level`` form field). We still honour a ``gwl`` key if a
        file happens to carry one.
        """
        try:
            return float(self.header.get("gwl", DEFAULT_GWL))
        except (TypeError, ValueError):
            return DEFAULT_GWL

    @property
    def max_depth(self) -> float:
        """Deepest reading in the sounding (m)."""
        return self.rows[-1].z if self.rows else 0.0


def _coerce_float(value: str, *, label: str, line_no: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"CPT parse error on data line {line_no}: {label!r}={value!r} "
            f"is not a number"
        ) from exc


def _parse_kv_line(line: str) -> Dict[str, str]:
    """Parse one ``KEY=value,KEY=value,...`` line into a dict.

    Tokens without an ``=`` (or empty) are skipped so stray commas / trailing
    separators do not break parsing. Keys are kept verbatim (CPTLOG uses
    upper-case keys like ``MA``, ``D``, ``QC``).
    """
    out: Dict[str, str] = {}
    for token in line.split(","):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, _, val = token.partition("=")
        key = key.strip()
        if key:
            out[key] = val.strip()
    return out


def _resolve_area_ratio(header: Dict[str, str]) -> tuple[float, str]:
    """Resolve the cone net area ratio and where it came from.

    The CPTLOG net-area field is ``MA`` (accepted case-insensitively). Its value
    is used verbatim; a file without MA falls back to :data:`DEFAULT_AREA_RATIO`.
    ``area_ratio_source`` records the origin ("MA" or "default").
    """
    for key in ("MA", "ma", "Ma"):
        if key in header:
            try:
                return float(header[key]), "MA"
            except (TypeError, ValueError):
                break
    return DEFAULT_AREA_RATIO, "default"


def _row_from_kv(kv: Dict[str, str], line_no: int) -> "CptRow | None":
    """Build a :class:`CptRow` from a parsed data line, or None if the line is
    not a reading (no depth ``D``). fs/u2 default to 0.0 when absent so a row
    that omits an optional channel still parses."""
    if "D" not in kv or "QC" not in kv:
        return None
    return CptRow(
        z=_coerce_float(kv["D"], label="D", line_no=line_no),
        qc=_coerce_float(kv["QC"], label="QC", line_no=line_no),
        fs=_coerce_float(kv["FS"], label="FS", line_no=line_no) if "FS" in kv else 0.0,
        u2=_coerce_float(kv["U"], label="U", line_no=line_no) if "U" in kv else 0.0,
    )


def parse_cpt_text(text: str, *, source_name: str = "<text>") -> CptSounding:
    """Parse a CPTLOG sounding from raw text into a :class:`CptSounding`.

    Works for uploaded files (parse straight from decoded bytes, no temp file).
    A line-by-line state machine: ``$`` and ``#`` are markers; comma-separated
    KEY=value lines before the ``#`` marker accumulate into ``header``; lines
    after it are readings. A line that looks like a reading (has ``D`` and
    ``QC``) is treated as data even if the ``#`` marker was missing, so a
    slightly non-standard file still parses. Blank lines are ignored and rows
    are returned sorted by depth.
    """
    header: Dict[str, str] = {}
    rows: List[CptRow] = []
    in_data = False

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line == _START_MARKER:
            continue
        if line.startswith(_DATA_MARKER):
            in_data = True
            continue

        kv = _parse_kv_line(line)
        if not kv:
            continue

        looks_like_reading = "D" in kv and "QC" in kv
        if in_data or looks_like_reading:
            in_data = True
            row = _row_from_kv(kv, line_no)
            if row is not None:
                rows.append(row)
        else:
            # Header line(s) before the data marker: merge the KEY=value pairs.
            header.update(kv)

    if not rows:
        raise ValueError(
            f"CPT file {source_name!r} contains no readings "
            f"(expected CPTLOG lines like 'D=..,QC=..,FS=..,U=..')"
        )

    rows.sort(key=lambda r: r.z)

    area_ratio, area_ratio_source = _resolve_area_ratio(header)
    return CptSounding(
        header=header,
        rows=rows,
        area_ratio=area_ratio,
        area_ratio_source=area_ratio_source,
    )


def parse_cpt(path: str) -> CptSounding:
    """Parse a CPTLOG sounding file at ``path`` into a :class:`CptSounding`."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return parse_cpt_text(fh.read(), source_name=path)
