#!/usr/bin/env python
"""Sniff + parse an instrument file and print what the parser found.

Standalone check for the instrument parsers: no DB, no network, no app
context. Prints the parser id, dataset kind, key metadata, array shapes /
dtypes, warnings, wall time and peak memory. Exit codes: 0 = parsed (or a
clean "no matching parser"), 1 = parse failure / unreadable path, 2 = usage.

Usage:
    python scripts/verify_parsers.py <file> [<file> ...] [--json] [--tracemalloc]

If an ``extraction_manifest.json`` sits next to an ODiSI pass file, the parsed
timestep count is compared with the manifest entry for that file (looking for
start/end style indices) and the boundary convention is reported -- the parser
is NEVER adjusted to make the numbers agree.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # python_backend/ on sys.path

from app.workspace.parsers import registry  # noqa: E402
from app.workspace.parsers.base import SNIFF_BYTES  # noqa: E402

KEY_META = (
    "source_filename", "file_size_bytes", "n_gages", "n_timesteps", "x_min_m",
    "x_max_m", "gage_pitch_mm", "sensor_length_m", "measurement_rate_hz",
    "sample_rate_hz", "sensor_serial", "tare_name", "units", "first_timestamp",
    "last_timestamp", "duration_s", "header_line_count",
    "n_samples", "n_channels", "channel_names", "sample_rate_hz",
    "record_first", "record_last", "column_min", "column_mean", "column_max", "n_time_gaps", "other_columns",
    "nan_count", "nan_fraction", "dead_gage_count", "dead_gage_indices", "tare_nan_count",
)


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _manifest_check(path: str, n_timesteps: int) -> dict:
    """Compare against a sibling extraction_manifest.json, if any (tolerant)."""
    mpath = os.path.join(os.path.dirname(os.path.abspath(path)), "extraction_manifest.json")
    if not os.path.exists(mpath):
        return {"manifest": None}
    try:
        with open(mpath, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {"manifest": mpath, "error": f"unreadable: {exc}"}
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    # Find the entry mentioning this file (search any list of dicts).
    entries = []
    def _walk(node):
        if isinstance(node, dict):
            if any(isinstance(v, str) and (base in v or stem in v or v in stem) and v for v in node.values()):
                entries.append(node)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
    _walk(manifest)
    entry = None
    for e in entries:
        vals = [v for v in e.values() if isinstance(v, str)]
        if any(base == v or stem == v or v.endswith(base) for v in vals):
            entry = e
            break
    if entry is None and entries:
        entry = entries[0]
    out = {"manifest": mpath, "header_lines": manifest.get("header_lines") if isinstance(manifest, dict) else None}
    if entry is None:
        out["entry"] = None
        return out
    out["entry"] = entry
    start = end = None
    for k, v in entry.items():
        kl = k.lower()
        if isinstance(v, (int, float)):
            if kl in ("start", "start_row", "start_index", "start_line", "row_start", "begin"):
                start = int(v)
            elif kl in ("end", "end_row", "end_index", "end_line", "row_end", "stop"):
                end = int(v)
    if start is not None and end is not None:
        span = end - start
        out["manifest_span"] = span
        if span == n_timesteps:
            out["convention"] = "end-exclusive (end - start == parsed timesteps)"
        elif span + 1 == n_timesteps:
            out["convention"] = "end-INCLUSIVE (end - start + 1 == parsed timesteps)"
        else:
            out["convention"] = f"MISMATCH: end - start = {span}, parsed = {n_timesteps}"
    for k in ("n_timesteps", "timesteps", "rows", "n_rows", "count"):
        if k in entry and isinstance(entry[k], (int, float)):
            out["manifest_count"] = int(entry[k])
            out["count_matches"] = int(entry[k]) == n_timesteps
    return out


def verify(path: str, as_json: bool, trace: bool = False) -> tuple[int, dict]:
    report: dict = {"file": path}
    if not os.path.isfile(path):
        report["error"] = "not a file"
        return 1, report
    with open(path, "rb") as fh:
        head = fh.read(SNIFF_BYTES)
    parser_id = registry.sniff(head)
    report["parser_id"] = parser_id
    if parser_id is None:
        report["result"] = "no matching parser (falls through to the document path)"
        return 0, report
    parser = registry.get(parser_id)
    report["dataset_kind"] = parser.dataset_kind

    rss_before = _rss_mb()
    t0 = time.perf_counter()
    try:
        result = parser.parse(path)
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 1, report
    elapsed = time.perf_counter() - t0
    report["elapsed_s"] = round(elapsed, 3)
    report["rss_before_mb"] = round(rss_before, 1)
    report["peak_rss_mb"] = round(_rss_mb(), 1)  # process peak RSS (ru_maxrss)
    if trace:
        # Optional second parse under tracemalloc: Python-heap peak (numpy
        # buffers included). Slows the parse ~5-8x, so it is opt-in and the
        # elapsed time above is always the untraced one.
        tracemalloc.start()
        parser.parse(path)
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report["peak_tracemalloc_mb"] = round(peak / 1e6, 1)
    report["metadata"] = {k: result.metadata[k] for k in KEY_META if k in result.metadata}
    report["shapes"] = {k: list(v) for k, v in result.shapes().items()}
    report["dtypes"] = result.dtypes()
    report["warnings"] = list(result.warnings)
    arrays = result.arrays
    if "x_axis" in arrays and arrays["x_axis"].size:
        report["x_axis_first"] = float(arrays["x_axis"][0])
        report["x_axis_last"] = float(arrays["x_axis"][-1])
    if "strain" in arrays:
        report["strain_dtype"] = str(arrays["strain"].dtype)
        report.update(_manifest_check(path, int(arrays["strain"].shape[0])))
    if "pressure" in arrays and arrays["pressure"].size:
        import numpy as np
        report["pressure_max_per_channel"] = [round(float(v), 3) for v in np.nanmax(arrays["pressure"], axis=0)]
        report["pressure_mean_per_channel"] = [
            round(float(v), 3) for v in np.nanmean(arrays["pressure"].astype(np.float64), axis=0)
        ]
    return 0, report


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    trace = "--tracemalloc" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    worst = 0
    for path in paths:
        code, report = verify(path, as_json, trace)
        worst = max(worst, code)
        if as_json:
            print(json.dumps(report, indent=2, default=str))
            continue
        print(f"== {path}")
        for k, v in report.items():
            if k == "file":
                continue
            if isinstance(v, (dict, list)):
                print(f"  {k}: {json.dumps(v, default=str)}")
            else:
                print(f"  {k}: {v}")
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
