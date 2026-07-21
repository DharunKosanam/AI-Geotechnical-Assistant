"""Deterministic tests for the CPT layer summary.

The pipeline (parse -> interpret -> summarize) is fully deterministic, so these
are pinned hard against known-good values for the committed sample sounding.
"""

import pytest

from app.workspace.calculators.cpt_interpretation import interpret_cpt
from app.workspace.data import SAMPLE_CPT_PATH
from app.workspace.interpretation.layer_summary import (
    MIN_LAYER_THICKNESS_M,
    _merge_flicker,
    stratigraphy_to_text,
    summarize,
)
from app.workspace.parsers.cpt import parse_cpt


@pytest.fixture(scope="module")
def strat():
    return summarize(interpret_cpt(parse_cpt(SAMPLE_CPT_PATH)))


@pytest.fixture(scope="module")
def results():
    return interpret_cpt(parse_cpt(SAMPLE_CPT_PATH))


# --- Pinned stratigraphy on the sample file --------------------------------

def test_sample_has_exactly_three_layers(strat):
    assert strat.n_layers == 3


def test_layer_boundaries_and_types(strat):
    l1, l2, l3 = strat.layers

    # Layer 1: near-surface silt mixtures (SBTn zone 4), 0.00-2.90 m.
    assert l1.depth_from == pytest.approx(0.0)
    assert l1.depth_to == pytest.approx(2.9)
    assert l1.thickness == pytest.approx(2.9)
    assert l1.sbt_zone == 4
    assert "Silt mixtures" in l1.dominant_sbt

    # Layer 2: sand (SBTn zone 6), 2.90-7.90 m.
    assert l2.depth_from == pytest.approx(2.9)
    assert l2.depth_to == pytest.approx(7.9)
    assert l2.thickness == pytest.approx(5.0)
    assert l2.sbt_zone == 6
    assert "Sand" in l2.dominant_sbt

    # Layer 3: clay (SBTn zone 3), 7.90-12.00 m (to max depth).
    assert l3.depth_from == pytest.approx(7.9)
    assert l3.depth_to == pytest.approx(12.0)
    assert l3.thickness == pytest.approx(4.1)
    assert l3.sbt_zone == 3
    assert "Clay" in l3.dominant_sbt


def test_layers_tile_the_profile_without_gaps(strat):
    # Contiguous, no gaps/overlaps, top at surface, bottom at max depth.
    assert strat.layers[0].depth_from == pytest.approx(0.0)
    assert strat.layers[-1].depth_to == pytest.approx(strat.max_depth)
    for upper, lower in zip(strat.layers, strat.layers[1:]):
        assert upper.depth_to == pytest.approx(lower.depth_from)


def test_metadata_carried_through(strat):
    assert strat.max_depth == pytest.approx(12.0)
    assert strat.gwl == pytest.approx(1.5)
    assert strat.area_ratio == pytest.approx(0.80)
    assert strat.area_ratio_source == "MA"


# --- Flicker merge (definition of done) ------------------------------------

def test_single_row_flicker_is_merged_out(results, strat):
    """The sample embeds ONE sand (zone-6) reading at 1.6 m inside the upper
    silt unit. It must be absorbed into layer 1, not surface as its own layer."""
    # The raw per-depth results contain exactly one zone-6 flicker in 0-2.9 m.
    flicker_rows = [r for r in results.rows if r.sbt_zone == 6 and r.z < 2.9]
    assert len(flicker_rows) == 1
    assert flicker_rows[0].z == pytest.approx(1.6)

    # After summarizing it is gone: layer 1 is a single uniform zone-4 unit that
    # spans the flicker depth and includes that reading in its row count.
    l1 = strat.layers[0]
    assert l1.sbt_zone == 4
    assert l1.depth_from <= 1.6 <= l1.depth_to
    assert l1.n_rows == 14  # includes the absorbed flicker reading


def test_merged_flicker_is_noted_on_layer_one(strat):
    # The absorbed zone-6 reading is transparently flagged so a reviewer knows
    # layer 1's qc/Ic ranges include an outlier.
    notes = " ".join(strat.layers[0].notes).lower()
    assert "merged anomalous reading" in notes


def test_no_layer_thinner_than_threshold(strat):
    for layer in strat.layers:
        assert layer.thickness >= MIN_LAYER_THICKNESS_M


def test_merge_flicker_unit_removes_single_row_spike():
    """Direct unit test of the merge on a synthetic zone sequence: a lone
    zone-6 spike between long zone-4 runs collapses away."""

    class _Row:
        def __init__(self, z):
            self.z = z

    zones = [4, 4, 4, 6, 4, 4, 4]          # single-row spike at index 3
    rows = [_Row(round(0.2 * (i + 1), 2)) for i in range(len(zones))]
    merged = _merge_flicker(zones, rows, dz=0.2)
    assert set(merged) == {4}               # spike absorbed, one zone remains


# --- Prompt-ready text -----------------------------------------------------

def test_stratigraphy_text_contains_layers_and_metadata(strat):
    text = stratigraphy_to_text(strat)
    assert "LAYERS (top to bottom):" in text
    assert "Groundwater level (GWL): 1.50 m" in text
    assert "Sand: clean sand to silty sand" in text
    assert "Clays: clay to silty clay" in text
    # No raw per-depth rows leak into the prompt text.
    assert text.count("Layer ") == strat.n_layers
