"""Sample data fixtures for the Engineering Workspace."""

from pathlib import Path

# CPTLOG-format sounding used by the deterministic tests, the route tests and
# the smoke run. This is a labelled SYNTHETIC STAND-IN (3 layers: silt over sand
# over clay, one merged single-row flicker; MA=0.80) in the real CPTLOG format.
# Swap in the real 040C6.CPT here when available and re-pin the layer tests.
SAMPLE_CPT_FILE = str(Path(__file__).resolve().parent / "sample_sounding.CPT")

# Back-compat alias: both names point at the same CPTLOG fixture.
SAMPLE_CPT_PATH = SAMPLE_CPT_FILE

__all__ = ["SAMPLE_CPT_PATH", "SAMPLE_CPT_FILE"]
