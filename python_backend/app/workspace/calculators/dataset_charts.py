"""Chart-payload helpers for dataset-bound calculators.

Charts are downsampled SERVER-SIDE to ~2,000 points per series before they
leave the backend -- never the full arrays (7,795 gages x 600 timesteps stays
in the .npz for the Excel export). Deterministic: pure numpy, no randomness.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

MAX_POINTS = 2000


def _clean(values: np.ndarray) -> List[Optional[float]]:
    """JSON-safe list: NaN/inf -> None, numpy scalars -> float, 6 sig. digits."""
    out: List[Optional[float]] = []
    for v in np.asarray(values, dtype=np.float64).tolist():
        if v is None or v != v or v in (float("inf"), float("-inf")):
            out.append(None)
        else:
            out.append(float(f"{v:.6g}"))
    return out


def downsample_xy(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int = MAX_POINTS,
    mode: str = "extreme",
) -> tuple[np.ndarray, np.ndarray]:
    """Reduce (x, y) to at most ``max_points`` points.

    ``mode="extreme"`` keeps, per bin, the sample with the largest |y| (peaks
    survive -- right for envelopes and influence lines); ``mode="mean"`` bins
    by mean. Below the limit the arrays are returned unchanged.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = x.shape[0]
    if n <= max_points:
        return x, y
    edges = np.linspace(0, n, max_points + 1).astype(int)
    xs = np.empty(max_points, dtype=np.float64)
    ys = np.empty(max_points, dtype=np.float64)
    for i in range(max_points):
        a, b = edges[i], max(edges[i + 1], edges[i] + 1)
        seg_y = y[a:b]
        seg_x = x[a:b]
        if mode == "mean":
            xs[i] = np.nanmean(seg_x) if seg_x.size else np.nan
            ys[i] = np.nanmean(seg_y) if seg_y.size and not np.all(np.isnan(seg_y)) else np.nan
        else:
            if np.all(np.isnan(seg_y)):
                xs[i], ys[i] = seg_x[0], np.nan
            else:
                j = int(np.nanargmax(np.abs(seg_y)))
                xs[i], ys[i] = seg_x[j], seg_y[j]
    return xs, ys


def series(name: str, x: np.ndarray, y: np.ndarray, mode: str = "extreme") -> Dict[str, Any]:
    xs, ys = downsample_xy(x, y, mode=mode)
    return {"name": name, "x": _clean(xs), "y": _clean(ys), "n_source": int(np.asarray(x).shape[0])}


def chart(
    chart_id: str,
    title: str,
    x_label: str,
    y_label: str,
    series_list: Sequence[Dict[str, Any]],
    markers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": chart_id,
        "title": title,
        "x_label": x_label,
        "y_label": y_label,
        "series": list(series_list),
    }
    if markers:
        payload["markers"] = markers
    return payload
