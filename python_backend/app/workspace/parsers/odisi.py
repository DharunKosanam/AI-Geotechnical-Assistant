"""Luna ODiSI 6xxx distributed-strain export (.tsv) parser.

``dataset_kind = "strain_distributed"``.

File layout (verified against the written brief; the real files must still be
checked with ``scripts/verify_parsers.py``):

* ~33 tab-separated ``Key<TAB>Value`` header lines (gage pitch, sensor length,
  measurement rate, sensor serial, tare name, units, ...),
* an optional separator line (dashes),
* data rows distinguished by column 0:

  ==========  ==================================  ================
  column 0    meaning                             values from
  ==========  ==================================  ================
  ``Tare``    zero-load baseline strain per gage  column 3 onward
  ``x-axis``  gage position along the fibre (m)   column 3 onward
  timestamp   one measurement (col 1 is           column 3 onward
              ``measurement``)
  ==========  ==================================  ================

Output arrays: ``x_axis`` (float64, n_gages), ``tare`` (float64, n_gages),
``strain`` (float32, n_timesteps x n_gages -- float64 would double memory for
no gain at microstrain precision), ``timestamps`` (datetime64[ms]) and
``timestamp_text`` (the raw timestamp strings). Strain is stored AS RECORDED.
NOTE (verified on the real pass 001): the ODiSI writes measurement rows
ALREADY relative to the named tare (per-gage median ~0 while the Tare row
spans thousands of microstrain); the Tare row is the reference baseline and
is kept alongside, not something to subtract again (see the DFOS calculator).

Gage quality is reported in ``metadata`` (never raised): ``nan_count``,
``nan_fraction``, ``dead_gage_count`` (gages NaN across ALL timesteps),
``dead_gage_indices`` (capped list) -- with a warning when dead gages exist
or the NaN fraction exceeds :data:`NAN_WARN_FRACTION`.

The file is streamed line by line in binary mode (exact byte progress) and is
never loaded whole as text; ``pandas.read_csv`` is deliberately not used -- the
header block plus three row kinds make it produce garbage.

Tolerated (appended to ``warnings``, never raised): tare/x-axis length
mismatch, measurement rows whose column count differs from the x-axis, a
header-declared gage count that contradicts the parsed arrays, unparseable
timestamps, non-numeric tokens. Raised (``ParserError``): the file has no
x-axis AND no measurement rows, i.e. it is not an ODiSI export at all.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.workspace.parsers.base import (
    Parser,
    ParserError,
    ParserResult,
    ProgressCallback,
)

PARSER_ID = "odisi_tsv"
DATASET_KIND = "strain_distributed"
LABEL = "DFOS"

# Values start at this column on every data row (Tare / x-axis / measurement),
# per the brief. If the x-axis row disagrees we auto-detect and WARN.
VALUE_COL = 3

# Gage-quality reporting: warn (information, not failure) above this NaN
# fraction, and cap the stored list of dead-gage indices (the count is always
# stored regardless).
NAN_WARN_FRACTION = 0.001
DEAD_GAGE_INDEX_CAP = 200

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
_SEPARATOR_RE = re.compile(r"^[-=_]{3,}\s*$")

# Header keys (lowercased, colon stripped) that identify an ODiSI export.
_SIGNATURE_KEYS = (
    "gage pitch",
    "sensor length",
    "measurement rate",
    "sensor serial",
    "tare name",
    "test name",
    "product",
    "units",
    "sensor name",
)


def _decode_head(head: bytes) -> str:
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    return head.decode("utf-8", errors="replace")


def sniff_odisi(head: bytes) -> bool:
    """True when the first 2 KB look like an ODiSI TSV header block.

    Requires the tab-separated key/value structure: at least three of the known
    header keys as line prefixes, or an explicit "ODiSI" mention plus at least
    one such key. A binary file (PDF, xlsx) or a plain CSV never matches.
    """
    text = _decode_head(head)
    if "\t" not in text:
        return False
    low = text.lower()
    keys_found = 0
    for line in low.splitlines():
        key = line.split("\t", 1)[0].strip().rstrip(":").strip()
        if not key:
            continue
        for sig in _SIGNATURE_KEYS:
            if key.startswith(sig):
                keys_found += 1
                break
    if keys_found >= 3:
        return True
    return "odisi" in low and keys_found >= 1


def _snake(key: str) -> str:
    key = key.strip().rstrip(":").strip().lower()
    key = re.sub(r"[^a-z0-9]+", "_", key)
    return key.strip("_")


def _typed(value: str) -> Any:
    v = value.strip()
    if v == "":
        return ""
    # Leading zeros mean "this is a label" (e.g. tare name "0409"), not a number.
    if re.fullmatch(r"[+-]?0\d+(\.\d*)?", v):
        return v
    try:
        f = float(v)
    except ValueError:
        return v
    if re.fullmatch(r"[+-]?\d+", v):
        return int(v)
    return f


_LEADING_NUMBER = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*[A-Za-z%/]*\s*$")


def _first_numeric(meta: Dict[str, Any], *needles: str) -> Optional[float]:
    """First numeric header value whose snake key contains ALL the needles.
    A value like ``"8.33333 Hz"`` (number + unit text, as the real export
    writes the measurement rate) counts as numeric."""
    for key, value in meta.items():
        if key.startswith("_"):
            continue
        if not all(n in key for n in needles):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            m = _LEADING_NUMBER.match(value)
            if m:
                return float(m.group(1))
    return None


def _first_text(meta: Dict[str, Any], *needles: str) -> Optional[str]:
    for key, value in meta.items():
        if key.startswith("_"):
            continue
        if all(n in key for n in needles) and isinstance(value, str) and value:
            return value
    return None


def _values(fields: List[str], value_col: int) -> List[str]:
    vals = fields[value_col:]
    while vals and vals[-1].strip() == "":
        vals.pop()
    return vals


def _to_float_row(vals: List[str], dtype, bad_counter: List[int]) -> np.ndarray:
    try:
        return np.array(vals, dtype=dtype)
    except ValueError:
        out = np.empty(len(vals), dtype=dtype)
        for i, v in enumerate(vals):
            try:
                out[i] = float(v)
            except ValueError:
                out[i] = np.nan
                bad_counter[0] += 1
        return out


def _fit(row: np.ndarray, n: int) -> np.ndarray:
    """Pad with NaN / truncate so every row has exactly ``n`` values."""
    if row.shape[0] == n:
        return row
    out = np.full(n, np.nan, dtype=row.dtype)
    m = min(n, row.shape[0])
    out[:m] = row[:m]
    return out


def _detect_value_col(fields: List[str]) -> Optional[int]:
    for i in range(1, min(len(fields), 8)):
        try:
            float(fields[i])
            return i
        except ValueError:
            continue
    return None


def _parse_timestamps(texts: List[str], warnings: List[str]) -> np.ndarray:
    if not texts:
        return np.array([], dtype="datetime64[ms]")
    try:
        return np.array([t.strip().replace(" ", "T", 1) for t in texts], dtype="datetime64[ms]")
    except (ValueError, TypeError):
        pass
    # Tolerant per-item parse; unparseable -> NaT (warned once).
    out = np.full(len(texts), np.datetime64("NaT"), dtype="datetime64[ms]")
    bad = 0
    for i, t in enumerate(texts):
        try:
            out[i] = np.datetime64(t.strip().replace(" ", "T", 1), "ms")
        except (ValueError, TypeError):
            bad += 1
    if bad:
        warnings.append(
            f"{bad} of {len(texts)} measurement timestamps could not be parsed "
            f"(kept as text in 'timestamp_text', NaT in 'timestamps')."
        )
    return out


def parse_odisi(path: str, progress: Optional[ProgressCallback] = None) -> ParserResult:
    """Stream an ODiSI .tsv file into a :class:`ParserResult`. See module doc."""
    warnings: List[str] = []
    raw_header: List[str] = []
    header: Dict[str, Any] = {}
    x_axis: Optional[np.ndarray] = None
    tare: Optional[np.ndarray] = None
    rows: List[np.ndarray] = []
    stamps: List[str] = []
    value_col = VALUE_COL
    value_col_detected = False
    ragged = 0
    ragged_example: Optional[Tuple[int, int]] = None
    bad_tokens = [0]
    dup_tare = dup_x = 0
    header_done = False
    line_no = 0

    total = os.path.getsize(path) or 1
    consumed = 0
    next_report = 0.0

    def _report() -> None:
        nonlocal next_report
        if progress is None:
            return
        frac = min(consumed / total, 1.0)
        if frac >= next_report:
            progress(frac)
            next_report = frac + 0.01

    with open(path, "rb") as fb:
        for raw in fb:
            line_no += 1
            consumed += len(raw)
            if line_no == 1 and raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.strip():
                continue  # blank lines carry nothing; data rows self-identify
            fields = line.split("\t")
            key0 = fields[0].strip()
            key0_low = key0.lower()

            is_tare = key0_low == "tare"
            is_x = key0_low == "x-axis"
            is_meas = bool(_TS_RE.match(key0)) or (
                len(fields) > 1 and fields[1].strip().lower() == "measurement"
            )

            if not (is_tare or is_x or is_meas):
                if _SEPARATOR_RE.match(line.strip()):
                    header_done = True
                    continue
                if header_done:
                    # Unknown row kind after the header: tolerate, note once.
                    if not any(w.startswith("Unrecognised row") for w in warnings):
                        warnings.append(
                            f"Unrecognised row kind at line {line_no} "
                            f"(column 0 = {key0[:32]!r}); such rows are skipped."
                        )
                    continue
                raw_header.append(line)
                if len(fields) >= 2:
                    header[_snake(fields[0])] = _typed("\t".join(fields[1:]).strip())
                else:
                    header[_snake(fields[0])] = ""
                continue

            header_done = True

            if is_x:
                if x_axis is not None:
                    dup_x += 1
                    continue
                if not value_col_detected:
                    try:
                        float(fields[value_col])
                    except (ValueError, IndexError):
                        detected = _detect_value_col(fields)
                        if detected is not None and detected != value_col:
                            warnings.append(
                                f"x-axis values do not start at column {value_col}; "
                                f"auto-detected column {detected} and used it for all rows."
                            )
                            value_col = detected
                    value_col_detected = True
                x_axis = _to_float_row(_values(fields, value_col), np.float64, bad_tokens)
            elif is_tare:
                if tare is not None:
                    dup_tare += 1
                    continue
                tare = _to_float_row(_values(fields, value_col), np.float64, bad_tokens)
            else:
                stamps.append(key0)
                rows.append(_to_float_row(_values(fields, value_col), np.float32, bad_tokens))
            _report()

    if x_axis is None and not rows:
        raise ParserError(
            "Not an ODiSI export: no 'x-axis' row and no measurement rows found."
        )

    # --- Reconcile lengths ---------------------------------------------------
    if x_axis is None:
        n_gages = int(rows[0].shape[0])
        warnings.append(
            f"No 'x-axis' row found; gage count {n_gages} taken from the first "
            f"measurement row and x_axis filled with NaN."
        )
        x_axis = np.full(n_gages, np.nan, dtype=np.float64)
    n_gages = int(x_axis.shape[0])

    if tare is None:
        warnings.append("No 'Tare' row found; tare filled with NaN.")
        tare = np.full(n_gages, np.nan, dtype=np.float64)
    elif tare.shape[0] != n_gages:
        warnings.append(
            f"Tare length {tare.shape[0]} differs from x-axis length {n_gages}; "
            f"tare padded/truncated to the x-axis."
        )
        tare = _fit(tare, n_gages)

    if rows:
        for i, r in enumerate(rows):
            if r.shape[0] != n_gages:
                ragged += 1
                if ragged_example is None:
                    ragged_example = (i, int(r.shape[0]))
                rows[i] = _fit(r, n_gages)
        strain = np.vstack(rows).astype(np.float32, copy=False)
    else:
        warnings.append("No measurement rows found (file contains only Tare / x-axis).")
        strain = np.empty((0, n_gages), dtype=np.float32)
    del rows
    if ragged:
        idx, got = ragged_example  # type: ignore[misc]
        warnings.append(
            f"{ragged} measurement row(s) had a column count different from the "
            f"x-axis ({n_gages}); first at timestep {idx} with {got} values. "
            f"Rows padded with NaN / truncated."
        )
    if dup_tare:
        warnings.append(f"{dup_tare} extra 'Tare' row(s) ignored (first kept).")
    if dup_x:
        warnings.append(f"{dup_x} extra 'x-axis' row(s) ignored (first kept).")
    if bad_tokens[0]:
        warnings.append(f"{bad_tokens[0]} non-numeric value token(s) replaced by NaN.")

    timestamps = _parse_timestamps(stamps, warnings)

    # --- Metadata ------------------------------------------------------------
    meta: Dict[str, Any] = dict(header)
    meta["_raw_header"] = raw_header
    meta["_value_column"] = value_col
    meta["header_line_count"] = len(raw_header)
    meta["source_filename"] = os.path.basename(path)
    meta["file_size_bytes"] = int(total)
    meta["n_gages"] = n_gages
    meta["n_timesteps"] = int(strain.shape[0])
    finite_x = x_axis[np.isfinite(x_axis)]
    meta["x_min_m"] = float(finite_x.min()) if finite_x.size else None
    meta["x_max_m"] = float(finite_x.max()) if finite_x.size else None

    # Canonical keys derived from the header (only if the header had them).
    pitch = _first_numeric(meta, "gage_pitch") or _first_numeric(meta, "gauge_pitch")
    if pitch is not None:
        meta.setdefault("gage_pitch_mm", pitch)
    elif finite_x.size >= 2:
        meta.setdefault("gage_pitch_mm", float(np.round(np.nanmedian(np.diff(finite_x)) * 1000.0, 4)))
    length = _first_numeric(meta, "sensor_length")
    if length is None and isinstance(header.get("length_m"), (int, float)):
        length = float(header["length_m"])  # real export writes "Length (m):"
    if length is not None:
        meta.setdefault("sensor_length_m", length)
    rate = _first_numeric(meta, "rate", "hz") or _first_numeric(meta, "measurement_rate")
    if rate is not None:
        meta.setdefault("measurement_rate_hz", rate)
    serial = _first_text(meta, "sensor_serial") or _first_text(meta, "serial")
    if serial:
        meta.setdefault("sensor_serial", serial)
    tare_name = _first_text(meta, "tare_name")
    if tare_name is None:
        tare_val = header.get("tare_name")
        tare_name = str(tare_val) if tare_val not in (None, "") else None
    if tare_name is not None:
        meta["tare_name"] = tare_name
    units = _first_text(meta, "units")
    if units:
        meta.setdefault("units", units)

    if timestamps.size:
        valid = timestamps[~np.isnat(timestamps)]
        if valid.size:
            meta["first_timestamp"] = str(valid[0])
            meta["last_timestamp"] = str(valid[-1])
            span = (valid[-1] - valid[0]) / np.timedelta64(1, "ms") / 1000.0
            meta["duration_s"] = float(span)
            if valid.size > 1 and span > 0:
                meta["sample_rate_hz"] = float(round((valid.size - 1) / span, 4))

    # --- Gage quality (information, never failure) ----------------------------
    nan_mask = np.isnan(strain)
    nan_count = int(nan_mask.sum())
    total_values = int(strain.size)
    nan_fraction = (nan_count / total_values) if total_values else 0.0
    dead = np.flatnonzero(nan_mask.all(axis=0)) if strain.shape[0] else np.array([], dtype=int)
    meta["nan_count"] = nan_count
    meta["nan_fraction"] = float(nan_fraction)
    meta["dead_gage_count"] = int(dead.size)
    meta["dead_gage_indices"] = [int(i) for i in dead[:DEAD_GAGE_INDEX_CAP]]
    meta["dead_gage_indices_truncated"] = bool(dead.size > DEAD_GAGE_INDEX_CAP)
    meta["tare_nan_count"] = int(np.isnan(tare).sum())
    if dead.size:
        shown = ", ".join(str(int(i)) for i in dead[:12]) + (", ..." if dead.size > 12 else "")
        warnings.append(
            f"{int(dead.size)} dead gage(s) (NaN across all {strain.shape[0]} timesteps): "
            f"indices {shown}."
        )
    if nan_fraction > NAN_WARN_FRACTION:
        warnings.append(
            f"{nan_count:,} NaN strain values ({nan_fraction:.2%} of {total_values:,}); "
            f"reductions ignore them (nan-aware)."
        )

    # Header-declared gage count vs parsed arrays (warn, never raise). Search
    # the HEADER keys only (derived keys such as dead_gage_count must not match).
    declared = _first_numeric(header, "gage", "count") or _first_numeric(header, "number", "gage")
    if declared is None:
        declared = _first_numeric(header, "num", "gage")
    if declared is not None and int(declared) != n_gages:
        warnings.append(
            f"Header declares {int(declared)} gages but the parsed x-axis has {n_gages}."
        )

    if progress is not None:
        progress(1.0)

    return ParserResult(
        parser_id=PARSER_ID,
        dataset_kind=DATASET_KIND,
        metadata=meta,
        arrays={
            "x_axis": x_axis,
            "tare": tare,
            "strain": strain,
            "timestamps": timestamps,
            "timestamp_text": np.array(stamps, dtype=str),
        },
        warnings=warnings,
    )


ODISI_PARSER = Parser(
    id=PARSER_ID,
    dataset_kind=DATASET_KIND,
    label=LABEL,
    extensions=(".tsv", ".txt"),
    sniff=sniff_odisi,
    parse=parse_odisi,
)
