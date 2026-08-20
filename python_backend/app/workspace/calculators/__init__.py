"""Deterministic calculators for the Engineering Workspace.

Exposes the low-level CPT math (unchanged) plus the pluggable calculator
registry that routes chat messages to a deterministic test.
"""

from app.workspace.calculators.base import Calculator, ComputeResult, DatasetInput, ParamSpec
from app.workspace.calculators.cpt_interpretation import (
    CptInterpretationResult,
    DepthResult,
    interpret_cpt,
)
from app.workspace.calculators.registry import (
    all_calculators,
    available_tests_text,
    get_calculator,
    match_calculator,
    parse_params,
)

__all__ = [
    "CptInterpretationResult",
    "DepthResult",
    "interpret_cpt",
    "Calculator",
    "ComputeResult",
    "DatasetInput",
    "ParamSpec",
    "all_calculators",
    "available_tests_text",
    "get_calculator",
    "match_calculator",
    "parse_params",
]
