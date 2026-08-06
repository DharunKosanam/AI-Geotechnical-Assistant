"""Unit tests for the calculator registry: matching, param parsing, listing.

Pure deterministic tests — no auth, no LLM, no network. They exercise the
explicit trigger routing, inline-parameter parsing, and the CPT plugin's
``compute`` (parse -> interpret_cpt -> summarize) against the sample sounding.
"""

from pathlib import Path

import pytest

from app.workspace.calculators import registry
from app.workspace.calculators.cpt import CPT_CALCULATOR
from app.workspace.data import SAMPLE_CPT_FILE

pytestmark = pytest.mark.unit


def _sample_text() -> str:
    return Path(SAMPLE_CPT_FILE).read_text(encoding="utf-8", errors="replace")


# --- Trigger matching (explicit only) --------------------------------------
@pytest.mark.parametrize(
    "message",
    ["run CPT", "please Run Cpt now", "run cpt, groundwater 2m", "interpret CPT"],
)
def test_match_cpt_triggers(message):
    assert registry.match_calculator(message) is CPT_CALCULATOR


@pytest.mark.parametrize("message", ["hello", "run terzaghi", "what tests exist?"])
def test_no_match_returns_none(message):
    assert registry.match_calculator(message) is None


def test_available_tests_text_lists_cpt():
    text = registry.available_tests_text()
    assert "CPT interpretation" in text
    assert "run cpt" in text.lower()


# --- Inline parameter parsing ----------------------------------------------
def test_parse_groundwater_and_unit_weight():
    params = registry.parse_params(
        "run cpt, groundwater 3.5m, unit weight 18", CPT_CALCULATOR
    )
    assert params["groundwater_level"] == 3.5
    assert params["soil_unit_weight"] == 18.0


def test_parse_partial_params():
    params = registry.parse_params("run cpt, gwl 2", CPT_CALCULATOR)
    assert params == {"groundwater_level": 2.0}


def test_parse_no_params_is_empty():
    assert registry.parse_params("run cpt", CPT_CALCULATOR) == {}


# --- CPT compute (deterministic) -------------------------------------------
def test_cpt_compute_produces_layers_rows_metadata():
    result = CPT_CALCULATOR.compute(_sample_text(), "sample_sounding.CPT", {})
    assert result.layers, "expected detected layers"
    # The calculator declares its export output as a table in the standard schema.
    assert len(result.tables) == 1
    table = result.tables[0]
    assert table["name"] == "CPT Data"
    assert table["rows"], "expected per-depth rows"
    # "Layer #" is the last column; every reading is assigned to a detected layer.
    layer_col = [c["header"] for c in table["columns"]].index("Layer #")
    assert all(row[layer_col] >= 1 for row in table["rows"])
    # A flat summary dict drives the Summary sheet.
    assert result.summary["Source file"] == "sample_sounding.CPT"
    assert result.metadata["source_file"] == "sample_sounding.CPT"
    assert result.metadata["reference"] == CPT_CALCULATOR.reference
    assert result.raw is not None


def test_cpt_compute_honours_param_overrides():
    result = CPT_CALCULATOR.compute(
        _sample_text(),
        "sample_sounding.CPT",
        {"groundwater_level": 4.0, "soil_unit_weight": 19.0},
    )
    assert result.metadata["groundwater_level"] == 4.0
    assert result.metadata["soil_unit_weight"] == 19.0
    assert result.metadata["unit_weight_source"] == "user"
