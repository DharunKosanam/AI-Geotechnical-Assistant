"""Event detection strategies for pressure-cell time series.

A strategy is a NAMED pure function ``(pressure, t_s, params) -> list[Event]``
registered in :data:`STRATEGIES`; the calculator picks one by name and reads
its parameters from config (``INSTRUMENT_EVENT_*``), optionally overridden
inline from the chat message. Adding a method = adding a function + a name,
not editing the calculator.

Default ``percentile_mad`` (per channel: percentile baseline over blocks ->
residual -> MAD-scaled noise -> k x noise threshold -> per-sample activity ->
debounce-merge into events). *** PROVISIONAL: not yet validated by the
supervising engineer. *** Everything here is deterministic numpy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

MAD_TO_SIGMA = 1.4826  # normal-consistency constant


@dataclass(frozen=True)
class DetectionParams:
    strategy: str = "percentile_mad"
    baseline_percentile: float = 20.0
    baseline_window_s: float = 300.0
    mad_multiplier: float = 6.0
    min_channels: int = 1
    merge_gap_s: float = 1.0
    min_duration_s: float = 0.3

    def describe(self) -> str:
        return (
            f"strategy={self.strategy}; baseline = p{self.baseline_percentile:g} per channel over "
            f"{self.baseline_window_s:g} s blocks (interpolated); noise = 1.4826 x MAD of residual; "
            f"threshold = {self.mad_multiplier:g} x noise; active if >= {self.min_channels} channel(s) "
            f"over threshold; merge gaps <= {self.merge_gap_s:g} s; drop events < {self.min_duration_s:g} s"
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Event:
    index: int
    start_idx: int  # inclusive sample index
    end_idx: int  # inclusive sample index
    # Filled by the caller (per-channel stats need channel names / timestamps).
    extra: Dict[str, Any] = field(default_factory=dict)


def block_percentile_baseline(y: np.ndarray, t_s: np.ndarray, percentile: float, window_s: float) -> np.ndarray:
    """Per-sample baseline: the percentile of ``y`` inside consecutive time
    blocks of ``window_s`` seconds, linearly interpolated between block
    centres (constant beyond the first/last centre). NaN-tolerant."""
    n = y.shape[0]
    if n == 0:
        return y.copy()
    span = float(t_s[-1] - t_s[0]) if n > 1 else 0.0
    if span <= 0 or window_s <= 0 or span < 2 * window_s:
        val = np.nanpercentile(y, percentile) if np.isfinite(y).any() else np.nan
        return np.full(n, val, dtype=np.float64)
    n_blocks = int(np.ceil(span / window_s))
    edges = t_s[0] + np.arange(n_blocks + 1) * window_s
    idx = np.searchsorted(t_s, edges)
    centres: List[float] = []
    values: List[float] = []
    for b in range(n_blocks):
        a, z = int(idx[b]), int(idx[b + 1])
        seg = y[a:z]
        if seg.size == 0 or not np.isfinite(seg).any():
            continue
        centres.append(float(np.nanmean(t_s[a:z])))
        values.append(float(np.nanpercentile(seg, percentile)))
    if not centres:
        return np.full(n, np.nan, dtype=np.float64)
    return np.interp(t_s, np.asarray(centres), np.asarray(values)).astype(np.float64)


def _runs(mask: np.ndarray) -> List[tuple[int, int]]:
    """Inclusive (start, end) index pairs of True runs."""
    if mask.size == 0 or not mask.any():
        return []
    d = np.diff(mask.astype(np.int8), prepend=0, append=0)
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1) - 1
    return list(zip(starts.tolist(), ends.tolist()))


def detect_percentile_mad(
    pressure: np.ndarray, t_s: np.ndarray, params: DetectionParams
) -> Dict[str, Any]:
    """Default strategy. Returns ``{"events": [Event], "baseline": (n x c),
    "noise": [c], "threshold": [c], "residual": (n x c)}``."""
    n, c = pressure.shape
    p = pressure.astype(np.float64)
    baseline = np.empty_like(p)
    noise = np.empty(c)
    for j in range(c):
        baseline[:, j] = block_percentile_baseline(p[:, j], t_s, params.baseline_percentile, params.baseline_window_s)
    residual = p - baseline
    for j in range(c):
        r = residual[:, j]
        r = r[np.isfinite(r)]
        med = np.median(r) if r.size else 0.0
        mad = np.median(np.abs(r - med)) if r.size else 0.0
        noise[j] = MAD_TO_SIGMA * mad
    threshold = params.mad_multiplier * noise
    with np.errstate(invalid="ignore"):
        over = residual > threshold[None, :]
    active = over.sum(axis=1) >= max(1, params.min_channels)

    # Sample interval for the debounce (median dt).
    dt = float(np.median(np.diff(t_s))) if n > 1 else 1.0
    gap_n = int(round(params.merge_gap_s / dt)) if dt > 0 else 0
    min_n = int(round(params.min_duration_s / dt)) if dt > 0 else 1

    runs = _runs(active)
    merged: List[tuple[int, int]] = []
    for s, e in runs:
        if merged and s - merged[-1][1] - 1 <= gap_n:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    events = [
        Event(index=i + 1, start_idx=s, end_idx=e)
        for i, (s, e) in enumerate((s, e) for s, e in merged if (e - s + 1) >= max(1, min_n))
    ]
    return {
        "events": events,
        "baseline": baseline,
        "noise": noise,
        "threshold": threshold,
        "residual": residual,
    }


Strategy = Callable[[np.ndarray, np.ndarray, DetectionParams], Dict[str, Any]]

STRATEGIES: Dict[str, Strategy] = {
    "percentile_mad": detect_percentile_mad,
}


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"Unknown event-detection strategy {name!r}; available: {sorted(STRATEGIES)}"
        ) from None
