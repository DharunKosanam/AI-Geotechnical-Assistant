"""Layer summary: noisy per-depth SBT -> compact engineering stratigraphy.

The deterministic calculator emits one :class:`DepthResult` per reading (dozens
to hundreds of rows). That is far too noisy to hand to an LLM and invites it to
recite raw numbers. :func:`summarize` collapses those rows into a handful of
contiguous engineering layers:

  * adjacent readings of the same soil-behaviour type are merged, and
  * single-reading "flicker" thinner than ``MIN_LAYER_THICKNESS_M`` is absorbed
    into the neighbouring layer so it does not masquerade as a real stratum.

The resulting :class:`Stratigraphy` -- NOT the raw rows -- is what feeds the AI
interpretation. It is a pure function of the calculator output: same input,
same layers, every time.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import List, Optional

from app.workspace.calculators.cpt_interpretation import (
    CptInterpretationResult,
    DepthResult,
)

# --- Tunable thresholds (confirm engineering values with Dr. Lin) -----------
# A contiguous run of the same SBT thinner than this is treated as flicker and
# merged into a neighbour rather than reported as its own layer. 0.40 m ~= two
# readings at a typical 0.20 m sampling interval: enough to reject single-point
# spikes while preserving genuine thin seams two readings thick or more.
MIN_LAYER_THICKNESS_M = 0.40

# Bq (pore-pressure ratio) note thresholds. High +Bq => soft fine-grained soil
# generating excess pore pressure; negative Bq => dilative / freely draining.
BQ_HIGH = 0.30
BQ_NEGATIVE = -0.10

# Cone-resistance note thresholds [MPa].
QC_VERY_LOW = 1.0    # soft / weak
QC_HIGH = 10.0       # dense / stiff


@dataclass
class Layer:
    """One contiguous engineering layer distilled from the per-depth results."""

    depth_from: float
    depth_to: float
    thickness: float
    sbt_zone: int
    dominant_sbt: str
    qc_min: float
    qc_max: float
    qc_mean: float
    ic_min: float
    ic_max: float
    ic_mean: float
    bq_mean: float
    n_rows: int
    notes: List[str] = field(default_factory=list)


@dataclass
class Stratigraphy:
    """Compact, LLM-ready summary of a sounding."""

    layers: List[Layer] = field(default_factory=list)
    max_depth: float = 0.0
    gwl: float = 0.0
    area_ratio: float = 0.80
    area_ratio_source: str = "default"

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    def soil_types(self) -> List[str]:
        """Distinct dominant SBT names present, in top-to-bottom order."""
        seen: List[str] = []
        for layer in self.layers:
            if layer.dominant_sbt not in seen:
                seen.append(layer.dominant_sbt)
        return seen


def _median_spacing(rows: List[DepthResult]) -> float:
    if len(rows) < 2:
        return 0.20  # sensible default for a single-row degenerate sounding
    diffs = [b.z - a.z for a, b in zip(rows, rows[1:]) if b.z > a.z]
    return statistics.median(diffs) if diffs else 0.20


def _initial_runs(zones: List[int]) -> List[List[int]]:
    """Group row indices into contiguous runs of equal SBT zone."""
    runs: List[List[int]] = []
    for idx, zone in enumerate(zones):
        if runs and zones[runs[-1][-1]] == zone:
            runs[-1].append(idx)
        else:
            runs.append([idx])
    return runs


def _run_thickness(run: List[int], rows: List[DepthResult], dz: float,
                   n_rows: int) -> float:
    """Thickness of a run using mid-point boundaries with the neighbours.

    Top of the profile is ground surface (0.0); bottom is the last reading.
    Interior boundaries sit half-way between the bounding readings, so the
    layers tile the profile with no gaps or overlaps.
    """
    top_idx, bot_idx = run[0], run[-1]
    depth_from = 0.0 if top_idx == 0 else (rows[top_idx - 1].z + rows[top_idx].z) / 2.0
    depth_to = (
        rows[bot_idx].z
        if bot_idx == n_rows - 1
        else (rows[bot_idx].z + rows[bot_idx + 1].z) / 2.0
    )
    return depth_to - depth_from


def _merge_flicker(zones: List[int], rows: List[DepthResult], dz: float) -> List[int]:
    """Absorb sub-threshold runs into a neighbour by reassigning their zone.

    Deterministic: each pass targets the single thinnest sub-threshold run,
    merges it into the thicker adjacent run (ties -> the upper neighbour), then
    re-derives runs so newly-adjacent equal zones coalesce. Terminates because
    every pass strictly reduces the run count.
    """
    zones = list(zones)
    n = len(zones)
    while True:
        runs = _initial_runs(zones)
        if len(runs) <= 1:
            break
        thin = [
            (i, _run_thickness(run, rows, dz, n))
            for i, run in enumerate(runs)
        ]
        sub = [(i, t) for i, t in thin if t < MIN_LAYER_THICKNESS_M]
        if not sub:
            break
        # Thinnest sub-threshold run first; stable tie-break on position.
        target_i = min(sub, key=lambda it: (it[1], it[0]))[0]
        run = runs[target_i]
        prev_run = runs[target_i - 1] if target_i > 0 else None
        next_run = runs[target_i + 1] if target_i < len(runs) - 1 else None

        if prev_run is None:
            donor_zone = zones[next_run[0]]
        elif next_run is None:
            donor_zone = zones[prev_run[0]]
        else:
            prev_t = _run_thickness(prev_run, rows, dz, n)
            next_t = _run_thickness(next_run, rows, dz, n)
            # Merge into the thicker neighbour; tie favours the upper layer.
            donor_zone = zones[prev_run[0]] if prev_t >= next_t else zones[next_run[0]]

        for idx in run:
            zones[idx] = donor_zone
    return zones


def _layer_notes(rows_slice: List[DepthResult], qc_mean: float,
                 bq_mean: float) -> List[str]:
    notes: List[str] = []
    if qc_mean < QC_VERY_LOW:
        notes.append(
            f"very low cone resistance (mean qc≈{qc_mean:.2f} MPa) — soft/weak soil"
        )
    elif qc_mean > QC_HIGH:
        notes.append(
            f"high cone resistance (mean qc≈{qc_mean:.1f} MPa) — dense/stiff soil"
        )
    if bq_mean > BQ_HIGH:
        notes.append(
            f"elevated pore-pressure response (mean Bq≈{bq_mean:.2f}) — "
            f"likely soft, normally consolidated fine-grained soil"
        )
    elif bq_mean < BQ_NEGATIVE:
        notes.append(
            f"negative pore-pressure response (mean Bq≈{bq_mean:.2f}) — "
            f"dilative / freely draining behaviour"
        )
    return notes


def _build_layer(run: List[int], rows: List[DepthResult], dz: float,
                 n_rows: int) -> Layer:
    top_idx, bot_idx = run[0], run[-1]
    depth_from = 0.0 if top_idx == 0 else (rows[top_idx - 1].z + rows[top_idx].z) / 2.0
    depth_to = (
        rows[bot_idx].z
        if bot_idx == n_rows - 1
        else (rows[bot_idx].z + rows[bot_idx + 1].z) / 2.0
    )
    slice_ = [rows[i] for i in run]
    qcs = [r.qc for r in slice_]
    ics = [r.Ic for r in slice_]
    bqs = [r.Bq for r in slice_]
    qc_mean = statistics.fmean(qcs)
    bq_mean = statistics.fmean(bqs)
    # Dominant SBT: the zone that occupies the most readings in the run. After
    # flicker merging every reading carries the run's assigned zone, so this is
    # simply that zone's name; computed from the mode for robustness.
    zone = statistics.mode([r.sbt_zone for r in slice_])
    dominant_name = next(r.sbt_name for r in slice_ if r.sbt_zone == zone)
    notes = _layer_notes(slice_, qc_mean, bq_mean)
    # Flag readings absorbed by the flicker merge whose own SBT differs from the
    # layer's dominant type, so a reviewer knows the qc/Ic ranges include them.
    n_merged = sum(1 for r in slice_ if r.sbt_zone != zone)
    if n_merged:
        notes.append(
            f"contains {n_merged} merged anomalous reading(s) of a different "
            f"soil-behaviour type (thin seam / flicker) — reported qc/Ic ranges "
            f"include these outliers"
        )
    return Layer(
        depth_from=round(depth_from, 3),
        depth_to=round(depth_to, 3),
        thickness=round(depth_to - depth_from, 3),
        sbt_zone=zone,
        dominant_sbt=dominant_name,
        qc_min=round(min(qcs), 3),
        qc_max=round(max(qcs), 3),
        qc_mean=round(qc_mean, 3),
        ic_min=round(min(ics), 3),
        ic_max=round(max(ics), 3),
        ic_mean=round(statistics.fmean(ics), 3),
        bq_mean=round(bq_mean, 3),
        n_rows=len(run),
        notes=notes,
    )


def summarize(results: CptInterpretationResult) -> Stratigraphy:
    """Collapse per-depth results into a compact, LLM-ready stratigraphy."""
    rows = results.rows
    strat = Stratigraphy(
        max_depth=results.max_depth,
        gwl=results.gwl,
        area_ratio=results.area_ratio,
        area_ratio_source=results.area_ratio_source,
    )
    if not rows:
        return strat

    dz = _median_spacing(rows)
    zones = _merge_flicker([r.sbt_zone for r in rows], rows, dz)
    n = len(rows)
    for run in _initial_runs(zones):
        strat.layers.append(_build_layer(run, rows, dz, n))
    return strat


def stratigraphy_to_text(strat: Stratigraphy) -> str:
    """Render the stratigraphy as the compact block handed to the LLM.

    Deterministic, numbers-only-from-Python text: metadata header plus one line
    per layer. This is the *only* soil data the model ever sees.
    """
    lines: List[str] = []
    lines.append("SOUNDING METADATA:")
    lines.append(f"- Maximum depth: {strat.max_depth:.2f} m")
    lines.append(f"- Groundwater level (GWL): {strat.gwl:.2f} m below ground")
    lines.append(
        f"- Cone area ratio a = {strat.area_ratio:.2f} "
        f"(source: {strat.area_ratio_source})"
    )
    lines.append(f"- Number of interpreted layers: {strat.n_layers}")
    lines.append("")
    lines.append("LAYERS (top to bottom):")
    for i, ly in enumerate(strat.layers, start=1):
        lines.append(
            f"Layer {i}: {ly.depth_from:.2f}–{ly.depth_to:.2f} m "
            f"(thickness {ly.thickness:.2f} m) — {ly.dominant_sbt} "
            f"[SBTn zone {ly.sbt_zone}]"
        )
        lines.append(
            f"    qc: min {ly.qc_min:.2f} / mean {ly.qc_mean:.2f} / "
            f"max {ly.qc_max:.2f} MPa; "
            f"Ic: min {ly.ic_min:.2f} / mean {ly.ic_mean:.2f} / "
            f"max {ly.ic_max:.2f}; mean Bq {ly.bq_mean:.2f}"
        )
        if ly.notes:
            for note in ly.notes:
                lines.append(f"    note: {note}")
    return "\n".join(lines)
