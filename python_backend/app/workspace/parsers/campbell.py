"""Campbell Scientific data-logger pressure-cell log (.dat / CSV) parser.

``dataset_kind = "pressure_timeseries"``.

File layout (verified against the written brief; the real file must still be
checked with ``scripts/verify_parsers.py``): a plain CSV with a real header
row ::

    TIMESTAMP,RECORD,TP4144_kPa,TP4145_kPa,TP4148_kPa,TP4149_kPa
    2024-05-10 00:00:00.000,249671,11.62,9.93,10.15,7.53
    ...

The parser is tolerant of the classic Campbell TOA5 dressing (a leading
``"TOA5",...`` environment line, then the field-name row, then a units row and
a processing row) and of double-quoted fields; those extra lines are kept in
``metadata["_raw_header"]`` and skipped. Data rows are streamed line by line
and converted in bounded chunks so peak memory stays well under the file size
times a small constant.

Channels are DETECTED: every header field ending in ``_kPa`` (case-insensitive)
is a pressure channel, in column order. The channel count is data, not a
constant. Other non-key columns are listed under ``metadata["other_columns"]``
and ignored.

Output arrays: ``timestamps`` (datetime64[ms]), ``record`` (int64), ``pressure``
(float32, n_samples x n_channels). Metadata: ``channel_names`` (column order),
``n_channels``, ``n_samples``, inferred ``sample_rate_hz`` /
``sample_interval_s``, ``first_timestamp`` / ``last_timestamp`` / ``duration_s``,
``record_first`` / ``record_last``, per-channel ``column_min/mean/max``.

Tolerated (warnings, never raised): rows with a wrong field count (skipped and
counted), non-numeric tokens (NaN), unparseable timestamps (NaT),
non-monotonic timestamps, gaps in RECORD or in time. Raised (``ParserError``):
no header row with TIMESTAMP + RECORD + at least one ``*_kPa`` column, or no
data rows at all.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import numpy as np

from app.workspace.parsers.base import (
    Parser,
    ParserError,
    ParserResult,
    ProgressCallback,
)

PARSER_ID = "campbell_dat"
DATASET_KIND = "pressure_timeseries"
LABEL = "Pressure"

# The header row may sit a few lines down in a TOA5-dressed file.
_HEADER_SEARCH_LINES = 8
_CHUNK_ROWS = 100_000
_KPA_RE = re.compile(r"_kpa$", re.IGNORECASE)
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def _decode_head(head: bytes) -> str:
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    return head.decode("utf-8", errors="replace")


def _split(line: str) -> List[str]:
    """CSV split for Campbell rows: comma-separated, optional double quotes,
    no embedded commas inside quoted fields in practice."""
    return [f.strip().strip('"').strip() for f in line.split(",")]


def _header_info(fields: List[str]) -> Optional[Dict[str, Any]]:
    """Recognise the field-name row; None if this is not it."""
    upper = [f.upper() for f in fields]
    if "TIMESTAMP" not in upper or "RECORD" not in upper:
        return None
    channels = [(i, f) for i, f in enumerate(fields) if _KPA_RE.search(f)]
    if not channels:
        return None
    return {
        "ts_col": upper.index("TIMESTAMP"),
        "rec_col": upper.index("RECORD"),
        "chan_cols": [i for i, _ in channels],
        "channel_names": [f for _, f in channels],
        "other_columns": [
            f for i, f in enumerate(fields)
            if i not in {upper.index("TIMESTAMP"), upper.index("RECORD")}
            and not _KPA_RE.search(f)
        ],
        "n_fields": len(fields),
    }


def sniff_campbell(head: bytes) -> bool:
    """True when one of the first few lines is a Campbell header row: comma-
    separated with TIMESTAMP and RECORD plus at least one ``*_kPa`` column.
    A generic CSV (no TIMESTAMP/RECORD/_kPa trio) never matches."""
    text = _decode_head(head)
    for line in text.splitlines()[:_HEADER_SEARCH_LINES]:
        if "," not in line:
            continue
        if _header_info(_split(line)) is not None:
            return True
    return False


def _flush(
    ts_buf: List[str],
    rec_buf: List[str],
    val_buf: List[List[str]],
    n_ch: int,
    out_ts: List[np.ndarray],
    out_rec: List[np.ndarray],
    out_val: List[np.ndarray],
    counters: Dict[str, int],
) -> None:
    if not ts_buf:
        return
    # timestamps
    try:
        ts = np.array([t.replace(" ", "T", 1) for t in ts_buf], dtype="datetime64[ms]")
    except (ValueError, TypeError):
        ts = np.full(len(ts_buf), np.datetime64("NaT"), dtype="datetime64[ms]")
        for i, t in enumerate(ts_buf):
            try:
                ts[i] = np.datetime64(t.replace(" ", "T", 1), "ms")
            except (ValueError, TypeError):
                counters["bad_ts"] += 1
    out_ts.append(ts)
    # record
    try:
        rec = np.array(rec_buf, dtype=np.int64)
    except (ValueError, TypeError):
        rec = np.empty(len(rec_buf), dtype=np.int64)
        for i, r in enumerate(rec_buf):
            try:
                rec[i] = int(float(r))
            except (ValueError, TypeError):
                rec[i] = -1
                counters["bad_rec"] += 1
    out_rec.append(rec)
    # values
    try:
        vals = np.array(val_buf, dtype=np.float32)
    except (ValueError, TypeError):
        vals = np.empty((len(val_buf), n_ch), dtype=np.float32)
        for i, row in enumerate(val_buf):
            for j, v in enumerate(row):
                try:
                    vals[i, j] = float(v)
                except (ValueError, TypeError):
                    vals[i, j] = np.nan
                    counters["bad_val"] += 1
    out_val.append(vals)
    ts_buf.clear()
    rec_buf.clear()
    val_buf.clear()


def parse_campbell(path: str, progress: Optional[ProgressCallback] = None) -> ParserResult:
    """Stream a Campbell .dat/.csv pressure log into a :class:`ParserResult`."""
    warnings: List[str] = []
    raw_header: List[str] = []
    info: Optional[Dict[str, Any]] = None
    header_line_index: Optional[int] = None
    counters = {"bad_ts": 0, "bad_rec": 0, "bad_val": 0, "wrong_width": 0}
    wrong_width_example: Optional[str] = None
    n_ch = 0
    ts_buf: List[str] = []
    rec_buf: List[str] = []
    val_buf: List[List[str]] = []
    out_ts: List[np.ndarray] = []
    out_rec: List[np.ndarray] = []
    out_val: List[np.ndarray] = []
    n_rows = 0

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
        for line_no, raw in enumerate(fb):
            consumed += len(raw)
            if line_no == 0 and raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.strip():
                continue
            if info is None:
                if line_no >= _HEADER_SEARCH_LINES:
                    break
                fields = _split(line)
                found = _header_info(fields) if "," in line else None
                raw_header.append(line)
                if found is not None:
                    info = found
                    header_line_index = line_no
                    n_ch = len(info["chan_cols"])
                continue
            fields = _split(line)
            if len(fields) != info["n_fields"]:
                counters["wrong_width"] += 1
                if wrong_width_example is None:
                    wrong_width_example = f"line {line_no + 1}: {len(fields)} fields"
                continue
            ts = fields[info["ts_col"]]
            if not _TS_RE.match(ts):
                # TOA5 units / processing rows sit right after the header.
                if n_rows == 0 and len(raw_header) < _HEADER_SEARCH_LINES + 2:
                    raw_header.append(line)
                    continue
                counters["bad_ts"] += 1
                # keep the row (NaT) rather than silently dropping data
            ts_buf.append(ts)
            rec_buf.append(fields[info["rec_col"]])
            val_buf.append([fields[i] for i in info["chan_cols"]])
            n_rows += 1
            if len(ts_buf) >= _CHUNK_ROWS:
                _flush(ts_buf, rec_buf, val_buf, n_ch, out_ts, out_rec, out_val, counters)
                _report()

    if info is None:
        raise ParserError(
            "Not a Campbell pressure log: no header row with TIMESTAMP, RECORD "
            "and at least one *_kPa column in the first lines."
        )
    _flush(ts_buf, rec_buf, val_buf, n_ch, out_ts, out_rec, out_val, counters)
    if n_rows == 0:
        raise ParserError("Campbell header found but the file has no data rows.")

    timestamps = np.concatenate(out_ts) if out_ts else np.array([], dtype="datetime64[ms]")
    record = np.concatenate(out_rec) if out_rec else np.array([], dtype=np.int64)
    pressure = (
        np.concatenate(out_val) if out_val else np.empty((0, n_ch), dtype=np.float32)
    ).astype(np.float32, copy=False)
    del out_ts, out_rec, out_val

    # --- Warnings from the counters ---------------------------------------
    if counters["wrong_width"]:
        warnings.append(
            f"{counters['wrong_width']} row(s) skipped for a wrong field count "
            f"(expected {info['n_fields']}); first at {wrong_width_example}."
        )
    if counters["bad_ts"]:
        warnings.append(f"{counters['bad_ts']} timestamp(s) could not be parsed (NaT).")
    if counters["bad_rec"]:
        warnings.append(f"{counters['bad_rec']} RECORD value(s) not integers (set to -1).")
    if counters["bad_val"]:
        warnings.append(f"{counters['bad_val']} non-numeric pressure token(s) replaced by NaN.")

    valid_ts = ~np.isnat(timestamps)
    meta: Dict[str, Any] = {
        "_raw_header": raw_header,
        "header_line_index": header_line_index,
        "source_filename": os.path.basename(path),
        "file_size_bytes": int(total),
        "channel_names": list(info["channel_names"]),
        "n_channels": n_ch,
        "n_samples": int(pressure.shape[0]),
        "other_columns": list(info["other_columns"]),
        "units": "kPa",
    }
    if record.size:
        meta["record_first"] = int(record[0])
        meta["record_last"] = int(record[-1])
        d = np.diff(record)
        jumps = int(np.count_nonzero(d != 1))
        if jumps:
            warnings.append(f"RECORD is not strictly sequential: {jumps} discontinuity(ies).")
    if valid_ts.any():
        vt = timestamps[valid_ts]
        meta["first_timestamp"] = str(vt[0])
        meta["last_timestamp"] = str(vt[-1])
        span_s = float((vt[-1] - vt[0]) / np.timedelta64(1, "ms")) / 1000.0
        meta["duration_s"] = span_s
        if vt.size > 1:
            diffs_ms = np.diff(vt).astype("timedelta64[ms]").astype(np.int64)
            med = float(np.median(diffs_ms))
            if med > 0:
                meta["sample_interval_s"] = med / 1000.0
                meta["sample_rate_hz"] = float(round(1000.0 / med, 4))
            neg = int(np.count_nonzero(diffs_ms < 0))
            if neg:
                warnings.append(f"Timestamps are not monotonic: {neg} backward step(s).")
            gaps = int(np.count_nonzero(diffs_ms > 1.5 * med)) if med > 0 else 0
            meta["n_time_gaps"] = gaps
            if gaps:
                warnings.append(
                    f"{gaps} gap(s) in the time series longer than 1.5x the "
                    f"median sample interval ({med / 1000.0:.3f} s)."
                )
    if pressure.size:
        with np.errstate(all="ignore"):
            meta["column_min"] = [float(v) for v in np.nanmin(pressure, axis=0)]
            meta["column_mean"] = [float(v) for v in np.nanmean(pressure.astype(np.float64), axis=0)]
            meta["column_max"] = [float(v) for v in np.nanmax(pressure, axis=0)]

    if progress is not None:
        progress(1.0)

    return ParserResult(
        parser_id=PARSER_ID,
        dataset_kind=DATASET_KIND,
        metadata=meta,
        arrays={"timestamps": timestamps, "record": record, "pressure": pressure},
        warnings=warnings,
    )


CAMPBELL_PARSER = Parser(
    id=PARSER_ID,
    dataset_kind=DATASET_KIND,
    label=LABEL,
    extensions=(".dat", ".csv"),
    sniff=sniff_campbell,
    parse=parse_campbell,
)
