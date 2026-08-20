"""Calculator registry + explicit message routing.

Holds the installed calculators and does EXPLICIT intent matching only: a chat
message is lowercased and checked against each calculator's ``trigger_phrases``.
There is NO auto-detection from document contents and NO fuzzy guessing — if no
trigger matches, the caller lists the available tests instead of running one.

Optional inline parameters (e.g. "groundwater 3.5m", "unit weight 18") are
parsed centrally here from the message text using each calculator's declared
``ParamSpec`` aliases.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.core import config
from app.workspace.calculators.base import Calculator, ParamSpec
from app.workspace.calculators.cpt import CPT_CALCULATOR
from app.workspace.calculators.dfos_pass_strain import DFOS_CALCULATOR
from app.workspace.calculators.traffic_load_monitoring import TRAFFIC_CALCULATOR

# Installed calculators, in matching order. The CPT interpretation is the
# original document-bound test; the instrument calculators are DATASET-bound
# (``required_dataset_kind`` set) and are only INSTALLED while
# INSTRUMENT_PARSERS_ENABLED is on (read at call time): with the flag off the
# installed tuple, the "I can run" text and the LLM router catalog are exactly
# the pre-feature ones.
CALCULATORS: Tuple[Calculator, ...] = (CPT_CALCULATOR, DFOS_CALCULATOR, TRAFFIC_CALCULATOR)


def _installed() -> Tuple[Calculator, ...]:
    if config.INSTRUMENT_PARSERS_ENABLED:
        return CALCULATORS
    return tuple(c for c in CALCULATORS if c.required_dataset_kind is None)


def all_calculators() -> Tuple[Calculator, ...]:
    """Every installed calculator, in registration order."""
    return _installed()


def get_calculator(calc_id: str) -> Optional[Calculator]:
    """Look up a calculator by its stable id, or None."""
    for calc in _installed():
        if calc.id == calc_id:
            return calc
    return None


def match_calculator(message: str) -> Optional[Calculator]:
    """Return the calculator whose trigger phrase appears in ``message``.

    Explicit-only: a case-insensitive substring match against each calculator's
    ``trigger_phrases``. First calculator with any matching phrase wins; None if
    nothing matches (the caller then lists available tests).
    """
    low = message.lower()
    for calc in _installed():
        if any(phrase in low for phrase in calc.trigger_phrases):
            return calc
    return None


def _match_param(message_low: str, spec: ParamSpec) -> Optional[float]:
    """Extract a single numeric value for one parameter from the message.

    Tries each alias longest-first (so "groundwater level" wins over
    "groundwater") and captures the first number that follows within a short
    gap, tolerating "=", ":", a unit word, etc. Returns None if unmatched or the
    captured token is not a number.
    """
    for alias in sorted(spec.aliases, key=len, reverse=True):
        pattern = re.escape(alias) + r"[^0-9\-]{0,10}(-?\d+(?:\.\d+)?)"
        m = re.search(pattern, message_low)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def parse_params(message: str, calc: Calculator) -> Dict[str, Any]:
    """Parse a calculator's optional inline parameters from the message.

    Only parameters actually present are returned; missing ones are omitted so
    the calculator falls back to its own defaults (file header / auto estimate).
    """
    low = message.lower()
    params: Dict[str, Any] = {}
    for spec in calc.optional_params:
        value = _match_param(low, spec)
        if value is not None:
            params[spec.key] = value
    return params


def available_tests_text() -> str:
    """Human-readable list of the tests and their trigger phrases.

    Used to reply when a message matches no trigger, e.g.:
    "I can run: CPT interpretation - say 'run CPT'."
    """
    installed = _installed()
    if not installed:
        return "No calculators are currently available."
    parts: List[str] = []
    for calc in installed:
        parts.append(f"{calc.name} - say '{calc.trigger_hint()}'")
    return "I can run: " + "; ".join(parts) + "."
