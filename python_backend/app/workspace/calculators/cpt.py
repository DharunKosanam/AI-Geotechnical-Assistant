"""CPT interpretation calculator plugin.

Wires the EXISTING deterministic CPT pipeline
(``parse_cpt_text`` -> ``interpret_cpt`` -> ``summarize``) and the AI
interpretation (``interpret_sounding``) behind the generic
:class:`~app.workspace.calculators.base.Calculator` interface. The engineering
math is NOT touched here: this module only structures the existing output into
a :class:`ComputeResult` (layers + per-depth rows + metadata) that the chat
route, the AI step and the Excel exporter all consume.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.workspace.calculators.base import Calculator, ComputeResult, ParamSpec
from app.workspace.calculators.cpt_interpretation import (
    CptInterpretationResult,
    DepthResult,
    interpret_cpt,
)
from app.workspace.interpretation.ai_interpret import interpret_sounding
from app.workspace.interpretation.layer_summary import Stratigraphy, summarize
from app.workspace.parsers.cpt import parse_cpt_text

# Standard/method reference quoted in the report and the Excel Summary sheet.
CPT_REFERENCE = (
    "Robertson (1990, 2009) normalized Soil Behaviour Type (SBTn); "
    "Robertson & Cabal (2010) unit-weight and area corrections."
)


def _layers_json(strat: Stratigraphy) -> List[Dict[str, Any]]:
    """Serialize detected layers for the on-screen results table."""
    return [
        {
            "layer": i,
            "depth_from": ly.depth_from,
            "depth_to": ly.depth_to,
            "thickness": ly.thickness,
            "sbt_zone": ly.sbt_zone,
            "soil_type": ly.dominant_sbt,
            "qc_mean": ly.qc_mean,
            "qc_min": ly.qc_min,
            "qc_max": ly.qc_max,
            "ic_mean": ly.ic_mean,
            "ic_min": ly.ic_min,
            "ic_max": ly.ic_max,
            "bq_mean": ly.bq_mean,
            "n_rows": ly.n_rows,
            "notes": ly.notes,
        }
        for i, ly in enumerate(strat.layers, start=1)
    ]


def _layer_number_for(z: float, strat: Stratigraphy) -> int:
    """Which detected layer a given depth belongs to (1-based; 0 if none).

    The layers tile the profile top-to-bottom with shared mid-point boundaries,
    so a depth on a boundary is assigned to the upper layer (first match wins).
    """
    for i, ly in enumerate(strat.layers, start=1):
        if ly.depth_from <= z <= ly.depth_to:
            return i
    if strat.layers:
        # Outside the tiled range (floating-point edge at very top/bottom).
        return 1 if z < strat.layers[0].depth_from else len(strat.layers)
    return 0


# Per-reading CPT Data table: (DepthResult attribute, column header, number
# format). ``None`` format = text column. This is the calculator's declared
# output schema for the Excel export; the generic export builder reads it.
_CPT_DATA_COLUMNS = (
    ("z", "Depth (m)", "0.00"),
    ("qc", "Cone Resistance qc (MPa)", "0.000"),
    ("qt", "Corrected qt (MPa)", "0.000"),
    ("fs", "Sleeve Friction fs (kPa)", "0.00"),
    ("Rf", "Friction Ratio Rf (%)", "0.000"),
    ("Fr", "Norm Friction Ratio Fr (%)", "0.000"),
    ("Ic", "Soil Behaviour Index Ic", "0.000"),
    ("Qtn", "Norm Cone Resistance Qtn", "0.000"),
    ("Bq", "Pore Pressure Ratio Bq", "0.000"),
    ("u0", "Hydrostatic u0 (kPa)", "0.00"),
    ("sigma_v0", "Total Vertical Stress (kPa)", "0.00"),
    ("sigma_v0_eff", "Effective Vertical Stress (kPa)", "0.00"),
    ("sbt_name", "Soil Behaviour Type", None),
    ("sbt_zone", "SBT Zone", "0"),
    # "layer" is not a DepthResult attribute; it is filled per-reading below.
    ("layer", "Layer #", "0"),
)


def _cpt_data_table(rows: List[DepthResult], strat: Stratigraphy) -> Dict[str, Any]:
    """Build the per-reading "CPT Data" table in the standard export schema.

    One row per deterministic ``DepthResult``; the ``layer`` column maps each
    reading to its detected layer number. Numbers stay numbers so the exporter
    writes real numeric cells.
    """
    columns = [
        {"header": header, "format": fmt} for _key, header, fmt in _CPT_DATA_COLUMNS
    ]
    table_rows = []
    for r in rows:
        layer_no = _layer_number_for(r.z, strat)
        table_rows.append(
            [
                layer_no if key == "layer" else getattr(r, key)
                for key, _header, _fmt in _CPT_DATA_COLUMNS
            ]
        )
    return {"name": "CPT Data", "columns": columns, "rows": table_rows}


def _cpt_summary(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Excel "Summary" sheet key/value rows from run metadata.

    (The generic exporter appends a "Date generated" row at export time.)
    """
    uw = metadata.get("soil_unit_weight")
    unit_weight = uw if isinstance(uw, (int, float)) else "auto (estimated per reading)"
    reference = metadata.get("reference", "")
    method = metadata.get("method", "")
    ref_text = f"{method} - {reference}" if method else reference
    return {
        "Source file": metadata.get("source_file", ""),
        "Groundwater level (m)": metadata.get("groundwater_level"),
        "Unit weight (kN/m3)": unit_weight,
        "Cone area ratio a": metadata.get("area_ratio"),
        "Area ratio source": metadata.get("area_ratio_source", ""),
        "Maximum depth (m)": metadata.get("max_depth"),
        "Number of layers": metadata.get("n_layers"),
        "Method / Standard reference": ref_text,
    }


def _summary_text(
    strat: Stratigraphy,
    results: CptInterpretationResult,
    filename: str,
    unit_weight: Optional[float],
) -> str:
    """One-line deterministic headline shown above the results table."""
    uw = (
        f"{unit_weight:.1f} kN/m3 (user)"
        if unit_weight is not None
        else "auto (Robertson & Cabal 2010)"
    )
    return (
        f"Detected {strat.n_layers} soil layer(s) over {results.max_depth:.2f} m. "
        f"GWL {results.gwl:.2f} m; cone area ratio a={results.area_ratio:.2f} "
        f"({results.area_ratio_source}); unit weight {uw}."
    )


def cpt_compute(text: str, filename: str, params: Dict[str, Any]) -> ComputeResult:
    """Run the deterministic CPT interpretation. Pure Python; no LLM.

    ``params`` may carry optional ``groundwater_level`` / ``soil_unit_weight``
    overrides parsed from the user's message; absent -> file header / auto
    estimate, exactly as the original form did.
    """
    gwl = params.get("groundwater_level")
    unit_weight = params.get("soil_unit_weight")

    sounding = parse_cpt_text(text, source_name=filename)
    results = interpret_cpt(sounding, gwl=gwl, unit_weight=unit_weight)
    strat = summarize(results)

    metadata = {
        "source_file": filename,
        "groundwater_level": results.gwl,
        "soil_unit_weight": unit_weight,
        "unit_weight_source": "user" if unit_weight is not None else "auto",
        "area_ratio": results.area_ratio,
        "area_ratio_source": results.area_ratio_source,
        "max_depth": results.max_depth,
        "n_layers": strat.n_layers,
        "reference": CPT_REFERENCE,
        "method": "Robertson SBTn (1990/2009)",
    }

    return ComputeResult(
        layers=_layers_json(strat),
        tables=[_cpt_data_table(results.rows, strat)],
        summary=_cpt_summary(metadata),
        metadata=metadata,
        summary_text=_summary_text(strat, results, filename, unit_weight),
        raw=results,
    )


async def cpt_interpret(raw: CptInterpretationResult) -> Dict[str, Any]:
    """AI plain-English interpretation of the deterministic result.

    Delegates to the existing ``interpret_sounding`` (same grounding contract
    and Ollama client as GeoPilot uses today). Serialized for the chat message.
    """
    interp = await interpret_sounding(raw)
    return {
        "narrative": interp.narrative,
        "per_layer_notes": interp.per_layer_notes,
        "flagged_concerns": interp.flagged_concerns,
        "is_ai_draft": interp.is_ai_draft,
        "model": interp.model,
    }


CPT_CALCULATOR = Calculator(
    id="cpt_interpretation",
    name="CPT interpretation",
    description=(
        "Interpret a CPT (cone penetration test) sounding: detect soil layers, "
        "soil behaviour type, qc/Ic, stresses, and flag engineering concerns."
    ),
    trigger_phrases=("run cpt", "cpt interpretation", "interpret cpt"),
    reference=CPT_REFERENCE,
    required_extension=".cpt",
    required_label="CPT sounding (.CPT)",
    optional_params=(
        ParamSpec(
            key="groundwater_level",
            label="groundwater level",
            unit="m",
            aliases=("groundwater level", "groundwater", "water table", "gwl"),
        ),
        ParamSpec(
            key="soil_unit_weight",
            label="unit weight",
            unit="kN/m3",
            aliases=("unit weight", "unit-weight", "soil unit weight", "gamma"),
        ),
    ),
    compute=cpt_compute,
    interpret=cpt_interpret,
)
