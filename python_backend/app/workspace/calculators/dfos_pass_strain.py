"""DFOS pass-strain calculator (dataset-bound: ``strain_distributed``).

Deterministic analysis of ONE vehicle pass recorded by a Luna ODiSI
distributed fibre-optic strain interrogator:

  * peak strain and its x-position per timestep (a DIRECT maximum over gages,
    NOT a fitted model),
  * max/min strain envelope along the fibre over the pass,
  * load position versus time and -- ONLY when the fit is credible -- the
    implied vehicle speed and direction,
  * influence line at a selectable gage (``gage_x`` in metres; default: the
    gage of the global peak),
  * a diagnostic max |strain| profile in fixed-width bands across the FULL
    fibre (trimmed regions included), reported without interpretation.

Tare convention (verified on the real pass 001, independently confirmed): the
ODiSI writes measurement rows ALREADY relative to the named tare -- the file's
``Tare`` row is the reference baseline. The recorded strain IS the
tare-relative strain and is NOT tared again (``DFOS_SUBTRACT_TARE`` = false).

Two SEPARATE fibre-end exclusions (PROVISIONAL, config-driven, metres):
``DFOS_LEADIN_EXCLUDE_M`` (default 1.10 m of fibre position x, unbonded lead-in
fibre reading near zero -- not damaged) at the head, and ``DFOS_TAIL_EXCLUDE_M`` (default 0.50 m,
fibre termination artifact) at the tail. Both are excluded from peak tracking,
envelope, speed fit and the global peak; the stored arrays and the export keep
the full fibre. Neither length has been validated by the supervising engineer
and the result says so (reference, visible notice, deterministic block).

Speed credibility: the implied speed and direction come from a least-squares
fit of peak position vs time; below ``DFOS_SPEED_MIN_R2`` (default 0.70) they
are reported as "not determinable" with the achieved R² -- no number anywhere
(block, card, export) -- and the result states that peak tracking did not
resolve a moving load on this dataset.

Bound to the dataset KIND, never to a file extension or parser id. Pure numpy,
no LLM: the same input always produces the same output. Charts are
downsampled server-side (see ``dataset_charts``).
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
from app.workspace.interpretation.dataset_interpret import interpret_dataset_result

CALCULATOR_ID = "dfos_pass_strain"
DATASET_KIND = "strain_distributed"

# Fraction of the global peak above which a timestep counts as "loaded" for
# the speed fit. Overridable inline ("peak fraction 0.3").
DEFAULT_PEAK_FRACTION = 0.2

LEADIN_REASON = "unbonded lead-in"
TAIL_REASON = "fibre termination artifact"
TRIM_PROVISIONAL_TEXT = (
    "Both exclusion lengths are PROVISIONAL - not validated by the supervising engineer."
)
NOT_DETERMINABLE = "not determinable"


def _reference(leadin_m: float, tail_m: float, subtract_tare: bool, min_r2: float) -> str:
    tare_txt = (
        "the ODiSI writes measurement rows ALREADY relative to the named 'Tare' (verified and "
        "independently confirmed on the real pass 001), so the recorded strain is used as the "
        "tare-relative strain and the Tare row is kept in the export, not subtracted again"
        if not subtract_tare
        else "measurement rows treated as ABSOLUTE strain and the file's 'Tare' row subtracted "
        "(DFOS_SUBTRACT_TARE=true)"
    )
    return (
        "Luna ODiSI 6xxx TSV export, microstrain; " + tare_txt + ". "
        f"Two separate fibre-end exclusions before peak tracking, envelope, speed fit and global "
        f"peak: lead-in {leadin_m:g} m at the head ({LEADIN_REASON}, reads near zero, not damaged) and "
        f"tail {tail_m:g} m at the far end ({TAIL_REASON}); the stored arrays and the export keep the "
        "full fibre. Both exclusion lengths are PROVISIONAL, pending validation by the supervising "
        "engineer. Peak tracking is a DIRECT per-timestep maximum over the analysed gages (no fitted "
        "model). Envelope = per-gage max/min over the pass. Implied speed = least-squares slope of "
        "peak position vs time over timesteps whose peak exceeds the peak fraction of the global "
        f"peak, reported ONLY when the fit R2 >= {min_r2:g}; otherwise speed and direction are 'not "
        "determinable' (peak tracking did not resolve a moving load). Influence line = strain history "
        "at one gage. Band profile = max |strain| per fixed-width band across the full fibre, "
        "reported without interpretation."
    )


def _fmt_ts(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    try:
        if np.isnat(ts):
            return None
    except TypeError:
        pass
    return str(np.datetime64(ts, "ms")).replace("T", " ")


def _time_seconds(timestamps: np.ndarray, n: int, rate_hz: Optional[float]) -> np.ndarray:
    """Seconds since the first sample (from timestamps; falls back to the rate)."""
    if timestamps is not None and timestamps.size == n and n > 0:
        try:
            ts = timestamps.astype("datetime64[ms]")
            valid = ~np.isnat(ts)
            if valid.any():
                t0 = ts[valid][0]
                secs = (ts - t0) / np.timedelta64(1, "ms") / 1000.0
                secs = np.where(valid, secs, np.nan)
                if np.isnan(secs).any():
                    idx = np.arange(n)
                    good = ~np.isnan(secs)
                    secs = np.interp(idx, idx[good], secs[good])
                return secs.astype(np.float64)
        except (TypeError, ValueError):
            pass
    dt = 1.0 / rate_hz if rate_hz and rate_hz > 0 else 1.0
    return np.arange(n, dtype=np.float64) * dt


def _nan_or(v: Any) -> Optional[float]:
    if v is None:
        return None
    v = float(v)
    return None if np.isnan(v) else v


def _num_param(params: Dict[str, Any], key: str, default: float, minimum: float = 0.0) -> float:
    v = params.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) >= minimum:
        return float(v)
    return float(default)


def band_profile(
    x: np.ndarray,
    rel_full: np.ndarray,
    band_width_m: float,
    lo: int,
    hi: int,
    high_level: float,
) -> List[Dict[str, Any]]:
    """Max |strain| per fixed-width band across the FULL fibre.

    Per band: x range, gage range, gage count, dead gages, max |strain| (over
    every timestep and gage in the band), median of the per-gage max |strain|,
    fraction of gages whose max |strain| exceeds ``high_level``, and which
    exclusion region the band falls in. Reporting only, no interpretation.
    """
    n_g = x.shape[0]
    with np.errstate(all="ignore"):
        absmax_gage = np.full(n_g, np.nan)
        col_ok = ~np.all(np.isnan(rel_full), axis=0)
        absmax_gage[col_ok] = np.nanmax(np.abs(rel_full[:, col_ok]), axis=0)
    x0 = float(x[0])
    x_end = float(x[-1])
    n_bands = int(np.ceil((x_end - x0) / band_width_m)) if band_width_m > 0 else 1
    n_bands = max(1, n_bands)
    out: List[Dict[str, Any]] = []
    for b in range(n_bands):
        bx0 = x0 + b * band_width_m
        bx1 = min(bx0 + band_width_m, x_end + 1e-9)
        idx = np.flatnonzero((x >= bx0 - 1e-9) & (x < bx1 if b < n_bands - 1 else x <= bx1))
        if idx.size == 0:
            continue
        g0, g1 = int(idx[0]), int(idx[-1])
        vals = absmax_gage[g0 : g1 + 1]
        finite = vals[np.isfinite(vals)]
        n_dead = int(idx.size - finite.size)
        if g1 < lo:
            region = f"lead-in (excluded: {LEADIN_REASON})"
        elif g0 >= hi:
            region = f"tail (excluded: {TAIL_REASON})"
        elif g0 < lo or g1 >= hi:
            region = "partly excluded"
        else:
            region = "analysed"
        out.append(
            {
                "band": b + 1,
                "x_from_m": round(float(x[g0]), 4),
                "x_to_m": round(float(x[g1]), 4),
                "gage_from": g0,
                "gage_to": g1,
                "n_gages": int(idx.size),
                "n_dead_gages": n_dead,
                "max_abs_strain": None if finite.size == 0 else float(finite.max()),
                "median_gage_max_abs_strain": None if finite.size == 0 else float(np.median(finite)),
                "fraction_gages_above_level": None if finite.size == 0 else float(np.mean(finite > high_level)),
                "region": region,
            }
        )
    return out


def dfos_compute(dataset: DatasetInput, filename: str, params: Dict[str, Any]) -> ComputeResult:
    """Deterministic pass-strain analysis. See module docstring."""
    arrays = dataset.arrays
    meta = dataset.metadata or {}
    x = np.asarray(arrays["x_axis"], dtype=np.float64)
    tare = np.asarray(arrays["tare"], dtype=np.float64)
    strain = np.asarray(arrays["strain"])
    if strain.ndim != 2 or strain.shape[1] != x.shape[0]:
        raise ValueError(
            f"strain array shape {strain.shape} does not match {x.shape[0]} gages."
        )
    n_t, n_g = strain.shape
    if n_t == 0:
        raise ValueError("The dataset has no measurement timesteps.")
    timestamps = arrays.get("timestamps")
    rate_hz = meta.get("sample_rate_hz") or meta.get("measurement_rate_hz")
    t_s = _time_seconds(timestamps, n_t, rate_hz)

    peak_fraction = params.get("peak_fraction", DEFAULT_PEAK_FRACTION)
    if not (0 < float(peak_fraction) < 1):
        peak_fraction = DEFAULT_PEAK_FRACTION
    peak_fraction = float(peak_fraction)

    # --- Two separate exclusions (metres, config-driven, provisional) ----------
    leadin_m = _num_param(params, "leadin_exclude_m", config.DFOS_LEADIN_EXCLUDE_M)
    tail_m = _num_param(params, "tail_exclude_m", config.DFOS_TAIL_EXCLUDE_M)
    x_first, x_last = float(x[0]), float(x[-1])
    # Lead-in: fibre POSITION x < leadin_m (x is measured along the fibre from
    # the interrogator's zero, so this is the first 1.10 m of fibre); tail: the
    # last tail_m metres before the far end.
    lo = int(np.searchsorted(x, leadin_m, side="left"))  # first analysed gage
    hi = int(np.searchsorted(x, x_last - tail_m, side="right"))  # exclusive end
    if hi - lo < 3:
        raise ValueError(
            f"Lead-in exclusion {leadin_m:g} m + tail exclusion {tail_m:g} m leave fewer than 3 of "
            f"{n_g} gages ({x_first:.3f}-{x_last:.3f} m) to analyse."
        )
    x_lo, x_hi = float(x[lo]), float(x[hi - 1])
    n_leadin_gages, n_tail_gages = lo, n_g - hi

    # --- Tare handling --------------------------------------------------------
    subtract_tare = bool(config.DFOS_SUBTRACT_TARE)
    rel_full = strain.astype(np.float64)
    if subtract_tare:
        rel_full = rel_full - tare[None, :]

    # --- Gage quality (full fibre + analysed span) -----------------------------
    nan_mask = np.isnan(strain)
    nan_count = int(nan_mask.sum())
    nan_fraction = float(nan_count / strain.size) if strain.size else 0.0
    dead_all = np.flatnonzero(nan_mask.all(axis=0))
    dead_in_span = dead_all[(dead_all >= lo) & (dead_all < hi)]

    rel = rel_full[:, lo:hi]
    xs = x[lo:hi]
    all_nan_rows = np.all(np.isnan(rel), axis=1)
    if all_nan_rows.all():
        raise ValueError("Every timestep is NaN inside the analysed span.")

    with np.errstate(all="ignore"):
        # --- Peak per timestep (direct maximum over the analysed gages) ---------
        peak_val = np.full(n_t, np.nan)
        peak_idx = np.zeros(n_t, dtype=np.int64)  # index within the analysed span
        min_val = np.full(n_t, np.nan)
        ok = ~all_nan_rows
        peak_val[ok] = np.nanmax(rel[ok], axis=1)
        peak_idx[ok] = np.nanargmax(rel[ok], axis=1)
        min_val[ok] = np.nanmin(rel[ok], axis=1)
        peak_x = np.where(ok, xs[peak_idx], np.nan)

        # --- Envelope: full fibre (export) and analysed span (analysis) ---------
        env_max_full = np.full(n_g, np.nan)
        env_min_full = np.full(n_g, np.nan)
        col_ok = ~np.all(np.isnan(rel_full), axis=0)
        env_max_full[col_ok] = np.nanmax(rel_full[:, col_ok], axis=0)
        env_min_full[col_ok] = np.nanmin(rel_full[:, col_ok], axis=0)
        env_t_full = np.zeros(n_g, dtype=np.int64)
        env_t_full[col_ok] = np.nanargmax(np.where(np.isnan(rel_full[:, col_ok]), -np.inf, rel_full[:, col_ok]), axis=0)
        env_max = env_max_full[lo:hi]
        env_min = env_min_full[lo:hi]

    # --- Global peak (analysed span) ---------------------------------------------
    g_t = int(np.nanargmax(peak_val))
    g_val = float(peak_val[g_t])
    g_gage = int(peak_idx[g_t]) + lo  # absolute gage index
    g_x = float(x[g_gage])
    peak_on_boundary = g_gage == lo or g_gage == hi - 1

    # --- Load position vs time + speed fit ---------------------------------------
    threshold = peak_fraction * g_val if g_val > 0 else np.inf
    loaded = ok & (peak_val >= threshold)
    n_loaded = int(loaded.sum())
    fit_speed = fit_direction = r2 = None
    loaded_span_s = None
    if n_loaded >= 3 and g_val > 0:
        tt = t_s[loaded]
        xx = peak_x[loaded]
        A = np.vstack([tt, np.ones_like(tt)]).T
        coef, *_ = np.linalg.lstsq(A, xx, rcond=None)
        slope = float(coef[0])
        pred = A @ coef
        ss_res = float(np.sum((xx - pred) ** 2))
        ss_tot = float(np.sum((xx - xx.mean()) ** 2))
        r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else None
        fit_speed = abs(slope)
        fit_direction = "+x (increasing fibre position)" if slope > 0 else "-x (decreasing fibre position)"
        loaded_span_s = float(tt.max() - tt.min())
    min_r2 = float(config.DFOS_SPEED_MIN_R2)
    speed_credible = r2 is not None and r2 >= min_r2 and fit_speed is not None
    # Suppression: below the credibility threshold NO speed/direction value is
    # emitted anywhere -- only "not determinable" plus the achieved R².
    speed = fit_speed if speed_credible else None
    direction = fit_direction if speed_credible else None
    r2_txt = f"{r2:.3f}" if r2 is not None else "n/a"
    speed_status = (
        f"reported (R² {r2_txt} >= {min_r2:g})"
        if speed_credible
        else f"{NOT_DETERMINABLE} (speed-fit R² {r2_txt} below the {min_r2:g} credibility threshold; "
        f"peak tracking did not resolve a moving load on this dataset)"
    )

    # --- Influence line at a selectable gage (inside the analysed span) ---------
    gage_x_req = params.get("gage_x")
    if gage_x_req is None:
        gage_idx = g_gage
    else:
        gage_idx = int(np.nanargmin(np.abs(xs - float(gage_x_req)))) + lo
    gage_x = float(x[gage_idx])
    influence = rel_full[:, gage_idx]

    # --- Band profile (full fibre, trimmed regions included) ----------------------
    bw = float(config.DFOS_BAND_WIDTH_M) if config.DFOS_BAND_WIDTH_M > 0 else 0.5
    high_level = float(config.DFOS_BAND_HIGH_STRAIN_MICROSTRAIN)
    bands_profile = band_profile(x, rel_full, bw, lo, hi, high_level)

    # --- Charts ---------------------------------------------------------------------
    trim_bands = []
    if n_leadin_gages > 0:
        trim_bands.append({"x0": x_first, "x1": x_lo, "label": f"lead-in {leadin_m:g} m excluded"})
    if n_tail_gages > 0:
        trim_bands.append({"x0": x_hi, "x1": x_last, "label": f"tail {tail_m:g} m excluded"})
    charts = [
        chart(
            "envelope",
            "Strain envelope along the fibre (analysed span; excluded ends shaded)",
            "Position along fibre x (m)",
            "Strain (microstrain)",
            [series("max", xs, env_max), series("min", xs, env_min)],
            markers=[{"x": g_x, "y": g_val, "label": f"global peak {g_val:.1f} µε @ {g_x:.2f} m"}],
        ),
        chart(
            "band_profile",
            f"Max |strain| per {bw:g} m band, full fibre (excluded ends shaded)",
            "Band start x (m)",
            "Max |strain| (microstrain)",
            [
                {
                    "name": "max |strain| per band",
                    "x": [b["x_from_m"] for b in bands_profile],
                    "y": [None if b["max_abs_strain"] is None else float(f"{b['max_abs_strain']:.6g}") for b in bands_profile],
                    "n_source": len(bands_profile),
                    "kind": "bar",
                }
            ],
        ),
        chart(
            "influence",
            f"Influence line at gage x = {gage_x:.2f} m",
            "Time since first sample (s)",
            "Strain (microstrain)",
            [series(f"x={gage_x:.2f} m", t_s, influence)],
        ),
        chart(
            "load_position",
            "Peak-strain position vs time (direct maximum, analysed span)",
            "Time since first sample (s)",
            "Position of peak x (m)",
            [series("peak position", t_s[ok], peak_x[ok], mode="mean")],
        ),
    ]
    charts[0]["x_range"] = [x_first, x_last]
    charts[0]["bands"] = trim_bands
    charts[1]["x_range"] = [x_first, x_last]
    charts[1]["bands"] = trim_bands
    charts[3]["y_range"] = [x_first, x_last]
    charts[3]["bands_y"] = trim_bands

    # --- Export tables (full precision, full fibre) ----------------------------------
    ts_text = arrays.get("timestamp_text")

    def _ts_at(i: int) -> str:
        if ts_text is not None and ts_text.size == n_t:
            return str(ts_text[i])
        return (_fmt_ts(timestamps[i]) or "") if timestamps is not None and timestamps.size == n_t else ""

    def _region_of(j: int) -> str:
        if j < lo:
            return f"no ({LEADIN_REASON}, excluded)"
        if j >= hi:
            return f"no ({TAIL_REASON}, excluded)"
        return "yes"

    peaks_table = {
        "name": "Peak per timestep",
        "columns": [
            {"header": "Timestep", "format": "0"},
            {"header": "Timestamp", "format": None},
            {"header": "Time (s)", "format": "0.000"},
            {"header": "Peak strain (microstrain)", "format": "0.000"},
            {"header": "Peak position x (m)", "format": "0.0000"},
            {"header": "Peak gage index", "format": "0"},
            {"header": "Min strain (microstrain)", "format": "0.000"},
            {"header": "Above peak fraction (fit input)", "format": None},
        ],
        "rows": [
            [i, _ts_at(i), float(t_s[i]), _nan_or(peak_val[i]), _nan_or(peak_x[i]),
             int(peak_idx[i]) + lo, _nan_or(min_val[i]), "yes" if loaded[i] else "no"]
            for i in range(n_t)
        ],
    }
    envelope_table = {
        "name": "Envelope",
        "columns": [
            {"header": "Gage index", "format": "0"},
            {"header": "Position x (m)", "format": "0.0000"},
            {"header": "In analysed span", "format": None},
            {"header": "Tare (microstrain)", "format": "0.000"},
            {"header": "Max strain (microstrain)", "format": "0.000"},
            {"header": "Min strain (microstrain)", "format": "0.000"},
            {"header": "Timestep of max", "format": "0"},
            {"header": "Dead gage (all NaN)", "format": None},
        ],
        "rows": [
            [j, float(x[j]), _region_of(j), _nan_or(tare[j]), _nan_or(env_max_full[j]),
             _nan_or(env_min_full[j]), int(env_t_full[j]), "yes" if not col_ok[j] else ""]
            for j in range(n_g)
        ],
    }
    influence_table = {
        "name": "Influence line",
        "columns": [
            {"header": "Timestep", "format": "0"},
            {"header": "Timestamp", "format": None},
            {"header": "Time (s)", "format": "0.000"},
            {"header": f"Strain at x={gage_x:.4f} m (microstrain)", "format": "0.000"},
        ],
        "rows": [[i, _ts_at(i), float(t_s[i]), _nan_or(influence[i])] for i in range(n_t)],
    }
    band_table = {
        "name": "Band profile",
        "columns": [
            {"header": "Band", "format": "0"},
            {"header": "x from (m)", "format": "0.0000"},
            {"header": "x to (m)", "format": "0.0000"},
            {"header": "Gage from", "format": "0"},
            {"header": "Gage to", "format": "0"},
            {"header": "Gages", "format": "0"},
            {"header": "Dead gages", "format": "0"},
            {"header": "Max |strain| (microstrain)", "format": "0.000"},
            {"header": "Median of per-gage max |strain| (microstrain)", "format": "0.000"},
            {"header": f"Fraction of gages with max |strain| > {high_level:g}", "format": "0.000"},
            {"header": "Region", "format": None},
        ],
        "rows": [
            [b["band"], b["x_from_m"], b["x_to_m"], b["gage_from"], b["gage_to"], b["n_gages"],
             b["n_dead_gages"], b["max_abs_strain"], b["median_gage_max_abs_strain"],
             b["fraction_gages_above_level"], b["region"]]
            for b in bands_profile
        ],
    }

    reference = _reference(leadin_m, tail_m, subtract_tare, min_r2)
    env_max_span = float(np.nanmax(env_max))
    env_min_span = float(np.nanmin(env_min))
    analysed_span_m = x_hi - x_lo
    fibre_span_m = x_last - x_first
    tare_txt = (
        "recorded strain used as tare-relative (Tare row not subtracted again)"
        if not subtract_tare
        else "Tare row subtracted from the recorded strain (DFOS_SUBTRACT_TARE=true)"
    )
    trim_status = "PROVISIONAL - both exclusion lengths pending engineering validation"

    metadata = {
        "source_file": filename,
        "dataset_id": dataset.id,
        "dataset_kind": dataset.dataset_kind,
        "n_gages": n_g,
        "n_timesteps": n_t,
        "gage_pitch_mm": meta.get("gage_pitch_mm"),
        "sample_rate_hz": rate_hz,
        "tare_name": meta.get("tare_name"),
        "tare_subtracted": subtract_tare,
        "tare_handling": tare_txt,
        "units": meta.get("units", "microstrain"),
        # exclusions + quality
        "leadin_exclude_m": leadin_m,
        "leadin_exclude_reason": LEADIN_REASON,
        "leadin_excluded_gages": n_leadin_gages,
        "tail_exclude_m": tail_m,
        "tail_exclude_reason": TAIL_REASON,
        "tail_excluded_gages": n_tail_gages,
        "trim_validation_status": trim_status,
        "analysed_gage_start": lo,
        "analysed_gage_end_exclusive": hi,
        "analysed_gage_count": hi - lo,
        "analysed_x_min_m": x_lo,
        "analysed_x_max_m": x_hi,
        "analysed_span_m": analysed_span_m,
        "fibre_x_min_m": x_first,
        "fibre_x_max_m": x_last,
        "nan_count": nan_count,
        "nan_fraction": nan_fraction,
        "dead_gage_count": int(dead_all.size),
        "dead_gages_in_analysed_span": int(dead_in_span.size),
        "dead_gage_indices": [int(i) for i in dead_all[:200]],
        # results
        "peak_fraction": peak_fraction,
        "global_peak_microstrain": g_val,
        "global_peak_x_m": g_x,
        "global_peak_gage_index": g_gage,
        "global_peak_on_trim_boundary": bool(peak_on_boundary),
        "global_peak_timestep": g_t,
        "global_peak_time_s": float(t_s[g_t]),
        "global_peak_timestamp": _ts_at(g_t),
        "envelope_max_microstrain": env_max_span,
        "envelope_min_microstrain": env_min_span,
        "envelope_max_full_fibre_microstrain": _nan_or(np.nanmax(env_max_full)),
        "envelope_min_full_fibre_microstrain": _nan_or(np.nanmin(env_min_full)),
        "loaded_timesteps": n_loaded,
        "loaded_span_s": loaded_span_s,
        "speed_fit_r2": r2,
        "speed_min_r2": min_r2,
        "speed_credible": bool(speed_credible),
        "speed_status": speed_status,
        "implied_speed_m_s": speed,  # None unless credible
        "implied_speed_kmh": None if speed is None else speed * 3.6,
        "direction": direction,  # None unless credible
        "influence_gage_index": gage_idx,
        "influence_gage_x_m": gage_x,
        "influence_peak_microstrain": _nan_or(np.nanmax(influence)) if np.isfinite(influence).any() else None,
        "band_width_m": bw,
        "band_high_strain_level": high_level,
        "band_profile": bands_profile,  # the values behind the band chart
        "reference": reference,
        "method": "Direct per-timestep maximum over the analysed span (lead-in + tail excluded); least-squares speed fit gated by R2",
        "export_prefix": "DFOS",
    }

    summary: Dict[str, Any] = {
        "Source file": filename,
        "Dataset kind": dataset.dataset_kind,
        "Gages (file)": n_g,
        "Timesteps": n_t,
        "Sample rate (Hz)": rate_hz,
        "Gage pitch (mm)": meta.get("gage_pitch_mm"),
        "Tare name": meta.get("tare_name"),
        "Tare handling": tare_txt,
        f"Lead-in excluded at head (m) - {LEADIN_REASON} (provisional)": leadin_m,
        "Lead-in excluded gages": n_leadin_gages,
        f"Tail excluded at far end (m) - {TAIL_REASON} (provisional)": tail_m,
        "Tail excluded gages": n_tail_gages,
        "Exclusion validation status": trim_status,
        "Analysed gages": hi - lo,
        "Analysed span (m)": f"{x_lo:.4f} to {x_hi:.4f} ({analysed_span_m:.3f} m of {fibre_span_m:.3f} m)",
        "Dead gages (all-NaN), whole fibre": int(dead_all.size),
        "Dead gages inside analysed span": int(dead_in_span.size),
        "NaN fraction (%)": round(100.0 * nan_fraction, 3),
        "Global peak strain (microstrain)": g_val,
        "Global peak position x (m)": g_x,
        "Global peak gage index": g_gage,
        "Global peak on an exclusion boundary": "yes" if peak_on_boundary else "no",
        "Global peak timestamp": _ts_at(g_t),
        "Envelope max, analysed span (microstrain)": env_max_span,
        "Envelope min, analysed span (microstrain)": env_min_span,
        "Timesteps above peak fraction (fit input)": n_loaded,
        "Peak fraction for speed fit": peak_fraction,
        "Speed fit R2": r2,
        "Speed credibility threshold R2": min_r2,
        "Load tracking": (
            "peak tracking resolved a moving load (fit credible)"
            if speed_credible
            else f"peak tracking did not resolve a moving load on this dataset (R² {r2_txt} < {min_r2:g})"
        ),
        "Implied speed (m/s)": speed if speed_credible else f"{NOT_DETERMINABLE} (R² {r2_txt})",
        "Implied speed (km/h)": (speed * 3.6) if speed_credible else NOT_DETERMINABLE,
        "Direction of travel": direction if speed_credible else NOT_DETERMINABLE,
        "Influence line gage x (m)": gage_x,
        "Peak tracking": "direct maximum per timestep (not a fitted model)",
        f"Band profile ({bw:g} m bands, full fibre)": f"{len(bands_profile)} bands - see 'Band profile' sheet",
        "Method / Standard reference": reference,
    }

    speed_txt = (
        f"implied speed {speed:.2f} m/s ({speed * 3.6:.1f} km/h) {direction}, R² {r2_txt} over {n_loaded} loaded timesteps"
        if speed_credible
        else f"implied speed and direction {NOT_DETERMINABLE} - peak tracking did not resolve a moving "
        f"load on this dataset (speed-fit R² {r2_txt} < {min_r2:g}, {n_loaded} timesteps above the peak fraction)"
    )
    summary_text = (
        f"Analysed x = {x_lo:.2f}-{x_hi:.2f} m ({hi - lo} gages; lead-in {leadin_m:g} m and tail {tail_m:g} m "
        f"excluded, provisional; {int(dead_all.size)} dead gages, {100 * nan_fraction:.2f}% NaN). "
        f"Global peak {g_val:.1f} µε at x = {g_x:.2f} m, timestep {g_t}"
        f"{' (' + _ts_at(g_t) + ')' if _ts_at(g_t) else ''}"
        f"{' - on an exclusion boundary' if peak_on_boundary else ''}; envelope max {env_max_span:.1f} µε, "
        f"min {env_min_span:.1f} µε; {speed_txt}; influence line at gage x = {gage_x:.2f} m."
    )

    notices: List[Dict[str, str]] = [
        {
            "level": "provisional",
            "text": (
                f"Fibre-end exclusions: lead-in {leadin_m:g} m at the head ({LEADIN_REASON}; {n_leadin_gages} gages, "
                f"x < {x_lo:.3f} m) and tail {tail_m:g} m at the far end ({TAIL_REASON}; {n_tail_gages} gages, "
                f"x > {x_hi:.3f} m) are not used for peak tracking, envelope, speed fit or the global peak; "
                f"analysed span {analysed_span_m:.2f} m of {fibre_span_m:.2f} m. " + TRIM_PROVISIONAL_TEXT
                + " Full untrimmed data remains in the export."
            ),
        },
        {
            "level": "info",
            "text": "Peak tracking is a direct per-timestep maximum over the analysed gages, not a fitted model.",
        },
    ]
    if dead_all.size or nan_fraction > 0.001:
        notices.append(
            {
                "level": "info",
                "text": (
                    f"Gage quality: {int(dead_all.size)} dead gage(s) (NaN across all timesteps"
                    f"{', ' + str(int(dead_in_span.size)) + ' inside the analysed span' if dead_all.size else ''}); "
                    f"{nan_count:,} NaN values ({100 * nan_fraction:.2f}%). Reductions ignore NaN."
                ),
            }
        )
    if not speed_credible:
        notices.append(
            {
                "level": "warning",
                "text": (
                    f"Peak tracking did not resolve a moving load on this dataset: speed-fit R² {r2_txt} is below "
                    f"the {min_r2:g} credibility threshold. Implied speed and direction of travel are {NOT_DETERMINABLE} "
                    "and are not reported."
                ),
            }
        )
    if peak_on_boundary:
        notices.append(
            {
                "level": "warning",
                "text": (
                    f"The global peak lies on an exclusion boundary (gage {g_gage}, x = {g_x:.3f} m): the excluded "
                    "region may not cover the artifact. Recorded for engineering review; the exclusion was not widened."
                ),
            }
        )

    raw = {
        "calculator": CALCULATOR_ID,
        "source_file": filename,
        "n_gages": n_g,
        "n_timesteps": n_t,
        "leadin_excluded_m": leadin_m,
        "tail_excluded_m": tail_m,
        "analysed_span_m": [round(x_lo, 3), round(x_hi, 3)],
        "dead_gage_count": int(dead_all.size),
        "nan_fraction_percent": round(100 * nan_fraction, 3),
        "global_peak_microstrain": round(g_val, 2),
        "global_peak_x_m": round(g_x, 3),
        "global_peak_on_exclusion_boundary": bool(peak_on_boundary),
        "envelope_max_microstrain": round(env_max_span, 2),
        "envelope_min_microstrain": round(env_min_span, 2),
        "speed_fit_r2": None if r2 is None else round(r2, 3),
        "implied_speed_m_s": round(speed, 3) if speed_credible else NOT_DETERMINABLE,
        "direction": direction if speed_credible else NOT_DETERMINABLE,
        "load_tracking": summary["Load tracking"],
        "influence_gage_x_m": round(gage_x, 3),
        "notes": [
            "Peak tracking is a direct maximum, not a fitted model.",
            TRIM_PROVISIONAL_TEXT,
        ]
        + ([] if speed_credible else ["Speed and direction are not determinable; do not state a vehicle speed."]),
    }

    return ComputeResult(
        layers=[],
        tables=[peaks_table, envelope_table, influence_table, band_table],
        summary=summary,
        metadata=metadata,
        summary_text=summary_text,
        raw=raw,
        charts=charts,
        segments=[],
        notices=notices,
    )


DFOS_CALCULATOR = Calculator(
    id=CALCULATOR_ID,
    name="DFOS pass strain",
    description=(
        "Analyse one vehicle pass of distributed fibre-optic strain (Luna ODiSI): "
        "peak strain and position per timestep, strain envelope along the fibre, "
        "load position vs time and implied speed, influence line at a gage."
    ),
    trigger_phrases=(
        "run dfos pass strain",
        "dfos pass strain",
        "analyse strain pass",
        "analyze strain pass",
        "fibre optic strain",
        "fiber optic strain",
        "run dfos",
    ),
    reference=_reference(
        config.DFOS_LEADIN_EXCLUDE_M, config.DFOS_TAIL_EXCLUDE_M, config.DFOS_SUBTRACT_TARE, config.DFOS_SPEED_MIN_R2
    ),
    required_extension="",
    required_label="DFOS strain dataset (ODiSI .tsv)",
    optional_params=(
        ParamSpec(
            key="gage_x",
            label="influence-line gage position",
            unit="m",
            aliases=("influence line at", "influence at", "gage at", "gauge at", "gage x", "at x"),
        ),
        ParamSpec(
            key="peak_fraction",
            label="peak fraction for the speed fit",
            unit="-",
            aliases=("peak fraction", "threshold fraction", "loaded fraction"),
        ),
        ParamSpec(
            key="leadin_exclude_m",
            label="lead-in excluded at the head",
            unit="m",
            aliases=("lead-in exclude", "leadin exclude", "lead in exclude", "head exclude"),
        ),
        ParamSpec(
            key="tail_exclude_m",
            label="tail excluded at the far end",
            unit="m",
            aliases=("tail exclude", "end exclude", "termination exclude"),
        ),
    ),
    compute=dfos_compute,
    interpret=interpret_dataset_result,
    required_dataset_kind=DATASET_KIND,
)
