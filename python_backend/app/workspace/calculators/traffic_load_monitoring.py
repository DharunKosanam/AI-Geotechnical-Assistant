"""Traffic-load monitoring calculator (dataset-bound: ``pressure_timeseries``).

Deterministic detection of load events (vehicle passes) in a day of buried
pressure-cell readings: per-channel baseline -> threshold -> debounce-merge
into events, via a NAMED, swappable strategy whose parameters come from config
(``INSTRUMENT_EVENT_*``; see ``event_detection``). Per event: start, end,
duration, peak kPa per channel (raw and above baseline), peak sum, and the
channel peak ORDER (cells lie in a line, so order gives direction of travel).
Day summary: event count, hourly histogram, peak distribution.

*** The default threshold method has NOT been approved by the supervising
engineer. Every output of this calculator carries a PROVISIONAL status: the
reference states it, the result card renders it inside the deterministic
block, the Excel Summary sheet carries it, and event counts are never described
as validated anywhere. ***

Bound to the dataset KIND, never to a file extension or parser id. Pure numpy;
same input + same parameters -> same output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from app.core import config
from app.workspace.calculators.base import (
    Calculator,
    ComputeResult,
    DatasetInput,
    ParamSpec,
)
from app.workspace.calculators.dataset_charts import chart, series
from app.workspace.calculators.event_detection import DetectionParams, get_strategy
from app.workspace.interpretation.dataset_interpret import interpret_dataset_result

CALCULATOR_ID = "traffic_load_monitoring"
DATASET_KIND = "pressure_timeseries"

PROVISIONAL_TEXT = (
    "Threshold method PROVISIONAL - pending validation by the supervising engineer. "
    "Event counts and event statistics from this run are NOT validated."
)


def _reference(params: DetectionParams) -> str:
    return (
        "Campbell pressure-cell log (kPa, per-channel). Event detection: "
        + params.describe()
        + ". Per event: start/end/duration, per-channel peak (raw and above baseline), "
        "peak sum (sum of per-channel peaks above baseline), channel peak order = direction "
        "of travel along the line of cells. STATUS: the threshold method and its parameters "
        "are PROVISIONAL and pending validation by the supervising engineer; counts are not "
        "validated."
    )


def _params_from_config(overrides: Dict[str, Any]) -> DetectionParams:
    def num(key: str, default: float) -> float:
        v = overrides.get(key)
        return float(v) if isinstance(v, (int, float)) else float(default)

    return DetectionParams(
        strategy=str(overrides.get("strategy") or config.INSTRUMENT_EVENT_STRATEGY),
        baseline_percentile=num("baseline_percentile", config.INSTRUMENT_EVENT_BASELINE_PERCENTILE),
        baseline_window_s=num("baseline_window_s", config.INSTRUMENT_EVENT_BASELINE_WINDOW_S),
        mad_multiplier=num("mad_multiplier", config.INSTRUMENT_EVENT_MAD_MULTIPLIER),
        min_channels=int(num("min_channels", config.INSTRUMENT_EVENT_MIN_CHANNELS)),
        merge_gap_s=num("merge_gap_s", config.INSTRUMENT_EVENT_MERGE_GAP_S),
        min_duration_s=num("min_duration_s", config.INSTRUMENT_EVENT_MIN_DURATION_S),
    )


def _time_seconds(timestamps: Optional[np.ndarray], n: int, rate_hz: Optional[float]) -> np.ndarray:
    if timestamps is not None and timestamps.size == n and n > 0:
        try:
            ts = timestamps.astype("datetime64[ms]")
            valid = ~np.isnat(ts)
            if valid.any():
                t0 = ts[valid][0]
                secs = (ts - t0) / np.timedelta64(1, "ms") / 1000.0
                secs = np.where(valid, secs, np.nan).astype(np.float64)
                if np.isnan(secs).any():
                    idx = np.arange(n)
                    good = ~np.isnan(secs)
                    secs = np.interp(idx, idx[good], secs[good])
                return secs
        except (TypeError, ValueError):
            pass
    dt = 1.0 / rate_hz if rate_hz and rate_hz > 0 else 1.0
    return np.arange(n, dtype=np.float64) * dt


def _ts_str(timestamps: Optional[np.ndarray], i: int) -> str:
    if timestamps is None or i >= timestamps.size:
        return ""
    v = timestamps[i]
    try:
        if np.isnat(v):
            return ""
    except TypeError:
        return str(v)
    return str(np.datetime64(v, "ms")).replace("T", " ")


def _hour_of(timestamps: Optional[np.ndarray], i: int, t_s: np.ndarray) -> int:
    if timestamps is not None and i < timestamps.size:
        try:
            v = np.datetime64(timestamps[i], "ms")
            if not np.isnat(v):
                secs = (v - v.astype("datetime64[D]")) / np.timedelta64(1, "s")
                return int(secs // 3600) % 24
        except (TypeError, ValueError):
            pass
    return int(t_s[i] // 3600) % 24


def traffic_compute(dataset: DatasetInput, filename: str, params: Dict[str, Any]) -> ComputeResult:
    """Deterministic event detection + day summary. See module docstring."""
    arrays = dataset.arrays
    meta = dataset.metadata or {}
    pressure = np.asarray(arrays["pressure"])
    if pressure.ndim != 2 or pressure.shape[0] == 0 or pressure.shape[1] == 0:
        raise ValueError(f"pressure array has unusable shape {pressure.shape}.")
    n, c = pressure.shape
    names: List[str] = list(meta.get("channel_names") or [f"ch{j + 1}" for j in range(c)])
    if len(names) != c:
        names = [f"ch{j + 1}" for j in range(c)]
    timestamps = arrays.get("timestamps")
    rate_hz = meta.get("sample_rate_hz")
    t_s = _time_seconds(timestamps, n, rate_hz)

    det = _params_from_config(params)
    strategy = get_strategy(det.strategy)
    out = strategy(pressure, t_s, det)
    events = out["events"]
    baseline = out["baseline"]
    residual = out["residual"]
    noise = out["noise"]
    threshold = out["threshold"]

    # --- Per-event statistics --------------------------------------------------
    rows: List[Dict[str, Any]] = []
    for ev in events:
        s, e = ev.start_idx, ev.end_idx
        seg_raw = pressure[s : e + 1].astype(np.float64)
        seg_res = residual[s : e + 1]
        with np.errstate(all="ignore"):
            pk_raw = np.nanmax(seg_raw, axis=0)
            pk_res = np.nanmax(seg_res, axis=0)
            pk_t = np.nanargmax(np.where(np.isnan(seg_res), -np.inf, seg_res), axis=0)
            inst_sum = np.nansum(seg_res, axis=1)
        order = [names[j] for j in np.argsort(pk_t, kind="stable")]
        # Direction: the cell that peaks first -> the cell that peaks last.
        direction = f"{order[0]} → {order[-1]}" if c > 1 else names[0]
        rows.append(
            {
                "index": ev.index,
                "start_idx": int(s),
                "end_idx": int(e),
                "start": _ts_str(timestamps, s),
                "end": _ts_str(timestamps, e),
                "start_s": float(t_s[s]),
                "duration_s": float(t_s[e] - t_s[s]) + (float(np.median(np.diff(t_s))) if n > 1 else 0.0),
                "peak_kpa": [float(v) for v in pk_raw],
                "peak_above_baseline_kpa": [float(v) for v in pk_res],
                "peak_time_s": [float(t_s[s + int(k)]) for k in pk_t],
                "peak_sum_kpa": float(np.nansum(pk_res)),
                "peak_sum_instant_kpa": float(np.nanmax(inst_sum)) if inst_sum.size else 0.0,
                "channel_order": order,
                "direction": direction,
                "hour": _hour_of(timestamps, s, t_s),
            }
        )

    n_events = len(rows)
    hourly = np.zeros(24, dtype=np.int64)
    for r in rows:
        hourly[r["hour"]] += 1
    peak_sums = np.array([r["peak_sum_kpa"] for r in rows], dtype=np.float64)
    if peak_sums.size:
        bins = np.linspace(0.0, float(np.ceil(peak_sums.max() / 5.0) * 5.0) or 5.0, 11)
        hist, _ = np.histogram(peak_sums, bins=bins)
    else:
        bins = np.linspace(0.0, 5.0, 11)
        hist = np.zeros(10, dtype=np.int64)
    largest = sorted(rows, key=lambda r: (-r["peak_sum_kpa"], r["index"]))[:5]

    # --- Plausibility ----------------------------------------------------------
    notices: List[Dict[str, str]] = [{"level": "provisional", "text": PROVISIONAL_TEXT + " Method: " + det.describe() + "."}]
    plausible = config.INSTRUMENT_EVENT_PLAUSIBLE_MIN <= n_events <= config.INSTRUMENT_EVENT_PLAUSIBLE_MAX
    if not plausible:
        notices.append(
            {
                "level": "warning",
                "text": (
                    f"Implausible event count ({n_events}) for this log "
                    f"(expected {config.INSTRUMENT_EVENT_PLAUSIBLE_MIN}-{config.INSTRUMENT_EVENT_PLAUSIBLE_MAX}): "
                    "the detection method/parameters are wrong for this data. Treat this run as a "
                    "method failure, not a result."
                ),
            }
        )

    # --- Segments (children of the dataset row) --------------------------------
    segments = [
        {
            "index": r["index"],
            "label": f"Event {r['index']} · {r['start'][11:19] if r['start'] else f'{r['start_s']:.0f} s'}",
            "start": r["start"] or None,
            "end": r["end"] or None,
            "start_idx": r["start_idx"],
            "end_idx": r["end_idx"],
            "duration_s": round(r["duration_s"], 3),
            "peak_sum_kpa": round(r["peak_sum_kpa"], 3),
            "peak_kpa": [round(v, 3) for v in r["peak_kpa"]],
            "channel_order": r["channel_order"],
            "direction": r["direction"],
        }
        for r in rows
    ]

    # --- Charts ------------------------------------------------------------------
    total_res = np.nansum(residual, axis=1)
    charts = [
        chart(
            "hourly",
            "Events per hour (PROVISIONAL detection)",
            "Hour of day",
            "Events",
            [{"name": "events", "x": list(range(24)), "y": [int(v) for v in hourly], "n_source": 24, "kind": "bar"}],
        ),
        chart(
            "peak_distribution",
            "Peak-sum distribution (PROVISIONAL detection)",
            "Peak sum above baseline (kPa), bin lower edge",
            "Events",
            [{"name": "events", "x": [float(f"{v:.6g}") for v in bins[:-1]], "y": [int(v) for v in hist], "n_source": 10, "kind": "bar"}],
        ),
        chart(
            "day_trace",
            "Total pressure above baseline over the day (downsampled)",
            "Time since first sample (s)",
            "Sum of channel residuals (kPa)",
            [series("total above baseline", t_s, total_res)],
            markers=[
                {"x": r["start_s"], "y": r["peak_sum_instant_kpa"], "label": f"E{r['index']}"} for r in largest
            ],
        ),
    ]

    # --- Tables (export) -----------------------------------------------------------
    ev_cols = [
        {"header": "Event #", "format": "0"},
        {"header": "Start", "format": None},
        {"header": "End", "format": None},
        {"header": "Duration (s)", "format": "0.00"},
    ]
    for nm in names:
        ev_cols.append({"header": f"Peak {nm} (kPa)", "format": "0.000"})
    for nm in names:
        ev_cols.append({"header": f"Peak above baseline {nm} (kPa)", "format": "0.000"})
    ev_cols += [
        {"header": "Peak sum above baseline (kPa)", "format": "0.000"},
        {"header": "Channel peak order", "format": None},
        {"header": "Direction of travel", "format": None},
        {"header": "Hour", "format": "0"},
        {"header": "Validation status", "format": None},
    ]
    events_table = {
        "name": "Events (provisional)",
        "columns": ev_cols,
        "rows": [
            [r["index"], r["start"], r["end"], r["duration_s"]]
            + r["peak_kpa"]
            + r["peak_above_baseline_kpa"]
            + [r["peak_sum_kpa"], " > ".join(r["channel_order"]), r["direction"], r["hour"], "PROVISIONAL - not validated"]
            for r in rows
        ],
    }
    hourly_table = {
        "name": "Hourly histogram",
        "columns": [{"header": "Hour", "format": "0"}, {"header": "Events (provisional)", "format": "0"}],
        "rows": [[h, int(hourly[h])] for h in range(24)],
    }
    dist_table = {
        "name": "Peak distribution",
        "columns": [
            {"header": "Peak sum bin low (kPa)", "format": "0.00"},
            {"header": "Peak sum bin high (kPa)", "format": "0.00"},
            {"header": "Events (provisional)", "format": "0"},
        ],
        "rows": [[float(bins[i]), float(bins[i + 1]), int(hist[i])] for i in range(len(hist))],
    }
    channel_table = {
        "name": "Channels",
        "columns": [
            {"header": "Channel", "format": None},
            {"header": "Baseline p-tile mean (kPa)", "format": "0.000"},
            {"header": "Noise 1.4826xMAD (kPa)", "format": "0.000"},
            {"header": "Threshold above baseline (kPa)", "format": "0.000"},
            {"header": "Max raw (kPa)", "format": "0.000"},
        ],
        "rows": [
            [names[j], float(np.nanmean(baseline[:, j])), float(noise[j]), float(threshold[j]), float(np.nanmax(pressure[:, j]))]
            for j in range(c)
        ],
    }

    reference = _reference(det)
    metadata: Dict[str, Any] = {
        "source_file": filename,
        "dataset_id": dataset.id,
        "dataset_kind": dataset.dataset_kind,
        "n_channels": c,
        "channel_names": names,
        "n_samples": n,
        "sample_rate_hz": rate_hz,
        "first_timestamp": meta.get("first_timestamp"),
        "last_timestamp": meta.get("last_timestamp"),
        "detection": det.as_dict(),
        "detection_description": det.describe(),
        "n_events": n_events,
        "plausible": plausible,
        "hourly_histogram": [int(v) for v in hourly],
        "peak_bins_kpa": [float(v) for v in bins],
        "peak_histogram": [int(v) for v in hist],
        "largest_events": [
            {k: r[k] for k in ("index", "start", "duration_s", "peak_sum_kpa", "peak_kpa", "channel_order", "direction")}
            for r in largest
        ],
        "channel_noise_kpa": [float(v) for v in noise],
        "channel_threshold_kpa": [float(v) for v in threshold],
        "validation_status": "PROVISIONAL - pending engineering validation",
        "reference": reference,
        "method": f"{det.strategy} (provisional)",
        "export_prefix": "TRAFFIC",
    }

    summary: Dict[str, Any] = {
        "Source file": filename,
        "Dataset kind": dataset.dataset_kind,
        "Validation status": "PROVISIONAL - threshold method pending engineering validation; counts NOT validated",
        "Channels": ", ".join(names),
        "Samples": n,
        "Sample rate (Hz)": rate_hz,
        "First sample": meta.get("first_timestamp"),
        "Last sample": meta.get("last_timestamp"),
        "Detection strategy": det.strategy,
        "Baseline percentile": det.baseline_percentile,
        "Baseline window (s)": det.baseline_window_s,
        "MAD multiplier": det.mad_multiplier,
        "Min channels over threshold": det.min_channels,
        "Merge gap (s)": det.merge_gap_s,
        "Min event duration (s)": det.min_duration_s,
        "Events detected (provisional)": n_events,
        "Busiest hour": int(np.argmax(hourly)) if n_events else None,
        "Largest event #": largest[0]["index"] if largest else None,
        "Largest event peak sum (kPa)": largest[0]["peak_sum_kpa"] if largest else None,
        "Largest event start": largest[0]["start"] if largest else None,
        "Method / Standard reference": reference,
    }
    for j, nm in enumerate(names):
        summary[f"Noise {nm} (kPa)"] = float(noise[j])
        summary[f"Threshold {nm} above baseline (kPa)"] = float(threshold[j])

    top = "; ".join(
        f"#{r['index']} {r['start'] or f'{r['start_s']:.0f} s'} peak-sum {r['peak_sum_kpa']:.1f} kPa ({r['direction']})"
        for r in largest[:3]
    )
    summary_text = (
        f"PROVISIONAL detection ({det.strategy}, k={det.mad_multiplier:g}): {n_events} event(s) over "
        f"{n:,} samples on {c} channel(s)"
        + (f"; busiest hour {int(np.argmax(hourly)):02d}:00" if n_events else "")
        + (f"; largest: {top}" if top else "")
        + ". Counts are not validated."
    )

    raw = {
        "calculator": CALCULATOR_ID,
        "source_file": filename,
        "validation_status": "PROVISIONAL - threshold method pending engineering validation; counts NOT validated",
        "detection": det.as_dict(),
        "n_events": n_events,
        "plausible": plausible,
        "hourly_histogram": [int(v) for v in hourly],
        "largest_events": metadata["largest_events"][:3],
        "channel_noise_kpa": [round(float(v), 3) for v in noise],
        "notes": [PROVISIONAL_TEXT],
    }

    return ComputeResult(
        layers=[],
        tables=[events_table, hourly_table, dist_table, channel_table],
        summary=summary,
        metadata=metadata,
        summary_text=summary_text,
        raw=raw,
        charts=charts,
        segments=segments,
        notices=notices,
    )


TRAFFIC_CALCULATOR = Calculator(
    id=CALCULATOR_ID,
    name="Traffic load monitoring",
    description=(
        "Detect vehicle load events in a day of pressure-cell readings: per-channel "
        "baseline + threshold, events with peaks per cell and direction of travel, hourly "
        "histogram and peak distribution (provisional threshold method)."
    ),
    trigger_phrases=(
        "run traffic load monitoring",
        "traffic load monitoring",
        "run traffic load",
        "detect load events",
        "pressure cell events",
        "count vehicle passes",
    ),
    reference=_reference(DetectionParams(
        strategy=config.INSTRUMENT_EVENT_STRATEGY,
        baseline_percentile=config.INSTRUMENT_EVENT_BASELINE_PERCENTILE,
        baseline_window_s=config.INSTRUMENT_EVENT_BASELINE_WINDOW_S,
        mad_multiplier=config.INSTRUMENT_EVENT_MAD_MULTIPLIER,
        min_channels=config.INSTRUMENT_EVENT_MIN_CHANNELS,
        merge_gap_s=config.INSTRUMENT_EVENT_MERGE_GAP_S,
        min_duration_s=config.INSTRUMENT_EVENT_MIN_DURATION_S,
    )),
    required_extension="",
    required_label="pressure-cell dataset (Campbell .dat)",
    optional_params=(
        ParamSpec(key="mad_multiplier", label="MAD multiplier", unit="-", aliases=("mad multiplier", "mad factor", "k =", "threshold multiplier")),
        ParamSpec(key="baseline_percentile", label="baseline percentile", unit="%", aliases=("baseline percentile", "percentile")),
        ParamSpec(key="merge_gap_s", label="merge gap", unit="s", aliases=("merge gap", "debounce")),
        ParamSpec(key="min_duration_s", label="minimum event duration", unit="s", aliases=("min duration", "minimum duration")),
        ParamSpec(key="min_channels", label="minimum channels over threshold", unit="-", aliases=("min channels", "minimum channels")),
    ),
    compute=traffic_compute,
    interpret=interpret_dataset_result,
    required_dataset_kind=DATASET_KIND,
)
