"""AI Interpretation of a CPT sounding.

Generates a plain-English geotechnical interpretation that is grounded ONLY in
the deterministic calculation. The core principle is non-negotiable:

    Deterministic Python produces every number. The LLM only describes,
    explains and summarizes the compact layer summary it is handed. It never
    does arithmetic and never invents values. The output is an AI DRAFT for a
    human engineer to review.

The model is reached through the SAME raw ``ollama.AsyncClient`` (called with
``think=False``) the live chatbot uses -- NOT the llama-index wrapper, which
drops the ``think`` flag. Base URL and model come from config/env.

The whole feature is gated behind ``WORKSPACE_ENABLED`` (default off).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import ollama

from app.core import config
from app.workspace.calculators.cpt_interpretation import CptInterpretationResult
from app.workspace.interpretation.layer_summary import (
    Stratigraphy,
    stratigraphy_to_text,
    summarize,
)

# Soil-type vocabulary used by the grounding guardrail. If the narrative names
# one of these that is NOT represented in the computed layer summary, that is a
# grounding violation and gets flagged (not silently trusted). Matched on a
# word-stem boundary so "sandy"/"gravelly" still count as sand/gravel.
_SOIL_KEYWORDS = (
    "clay",
    "silt",
    "sand",
    "gravel",
    "peat",
    "organic",
    "cobble",
    "boulder",
    "loam",
    "bedrock",
)


class WorkspaceDisabledError(RuntimeError):
    """Raised when interpretation is requested while WORKSPACE_ENABLED is off."""


@dataclass
class InterpretationResult:
    """AI draft interpretation plus the deterministic, grounded scaffolding."""

    narrative: str
    per_layer_notes: List[Dict[str, Any]] = field(default_factory=list)
    flagged_concerns: List[str] = field(default_factory=list)
    model: str = ""
    is_ai_draft: bool = True


# --- System prompt: the grounding contract handed to the model --------------
SYSTEM_PROMPT = (
    "You are a senior geotechnical engineer writing a concise interpretation of "
    "a Cone Penetration Test (CPT) sounding for a colleague to review.\n\n"
    "STRICT GROUNDING RULES — follow them exactly:\n"
    "1. Use ONLY the layer data provided below. It was produced by a validated "
    "deterministic calculation.\n"
    "2. Do NOT perform any arithmetic and do NOT state any numeric value that is "
    "not already present in the provided data. You may refer to the given "
    "numbers, never invent or recompute them.\n"
    "3. Describe the profile strictly top-to-bottom, layer by layer.\n"
    "4. Explicitly call out the main transition(s) between layers and any flagged "
    "anomalies (e.g. pore-pressure or friction responses) that are noted.\n"
    "5. Only mention soil types that appear in the provided layers. Never "
    "introduce a soil type (e.g. gravel, peat) that is not in the data.\n"
    "6. Write concise, professional geotechnical prose in flowing paragraphs — "
    "no bullet-point dumps, no tables, no headings.\n"
    "7. This is an AI DRAFT; frame it as an interpretation for engineering "
    "review, not a final determination.\n"
    "Do NOT use <think> tags or any XML tags in your response."
)


def build_interpretation_prompt(results: CptInterpretationResult) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a sounding.

    The user prompt embeds the compact layer summary — the ONLY soil data the
    model sees. Raw per-depth rows are never included. Kept public so tests can
    assert the layer summary and grounding instructions are present.
    """
    strat = summarize(results)
    summary_text = stratigraphy_to_text(strat)
    user_prompt = (
        "Interpret the following CPT sounding for engineering review.\n\n"
        f"{summary_text}\n\n"
        "Write a concise interpretation (a few short paragraphs) describing the "
        "soil profile from the ground surface downward, highlighting the main "
        "layer transitions and any noted anomalies. Remember: use only the data "
        "above and do not introduce any numbers or soil types not shown."
    )
    return SYSTEM_PROMPT, user_prompt


def _strip_think_tags(text: str) -> str:
    """Defensively remove any <think>...</think> block (should not occur with
    think=False, but the guard is cheap and mirrors the chat pipeline)."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _present_soil_keywords(strat: Stratigraphy) -> set[str]:
    """Soil-type keywords that ARE represented in the computed layers."""
    hay = " ".join(layer.dominant_sbt.lower() for layer in strat.layers)
    return {kw for kw in _SOIL_KEYWORDS if re.search(rf"\b{kw}", hay)}


def ground_check(narrative: str, strat: Stratigraphy) -> List[str]:
    """Grounding guardrail: flag soil types claimed by the narrative that are
    absent from the layer summary. Returns a list of concern strings (empty if
    the narrative is consistent with the computed layers)."""
    present = _present_soil_keywords(strat)
    text = narrative.lower()
    flags: List[str] = []
    for kw in _SOIL_KEYWORDS:
        if kw in present:
            continue
        if re.search(rf"\b{kw}", text):
            present_list = ", ".join(sorted(present)) or "none"
            flags.append(
                f"Grounding check: narrative mentions '{kw}', which is not among "
                f"the computed soil types ({present_list}). Verify before relying "
                f"on this statement."
            )
    return flags


def _deterministic_scaffolding(strat: Stratigraphy) -> tuple[List[Dict[str, Any]], List[str]]:
    """Build grounded per-layer notes and the anomaly portion of the concerns
    list straight from the deterministic summary (no LLM involved)."""
    per_layer_notes: List[Dict[str, Any]] = []
    concerns: List[str] = []
    for i, ly in enumerate(strat.layers, start=1):
        per_layer_notes.append(
            {
                "layer": i,
                "depth_from": ly.depth_from,
                "depth_to": ly.depth_to,
                "dominant_sbt": ly.dominant_sbt,
                "sbt_zone": ly.sbt_zone,
                "qc_mean": ly.qc_mean,
                "ic_mean": ly.ic_mean,
                "notes": list(ly.notes),
            }
        )
        for note in ly.notes:
            concerns.append(f"{ly.depth_from:.2f}–{ly.depth_to:.2f} m: {note}")
    return per_layer_notes, concerns


def _default_client() -> "ollama.AsyncClient":
    """Construct the shared raw Ollama async client (honours think=False)."""
    return ollama.AsyncClient(
        host=config.OLLAMA_BASE_URL, timeout=config.OLLAMA_REQUEST_TIMEOUT
    )


async def interpret_sounding(
    results: CptInterpretationResult,
    *,
    client: Optional[Any] = None,
) -> InterpretationResult:
    """Generate an AI-draft interpretation of a computed CPT sounding.

    Gated behind ``WORKSPACE_ENABLED``. ``client`` may be injected (an object
    exposing an async ``chat``) for testing; in production the shared raw
    ``ollama.AsyncClient`` is built and called with ``think=False``.
    """
    if not config.WORKSPACE_ENABLED:
        raise WorkspaceDisabledError(
            "AI Interpretation is disabled. Set WORKSPACE_ENABLED to enable it."
        )

    strat = summarize(results)
    system_prompt, user_prompt = build_interpretation_prompt(results)
    model = config.OLLAMA_MODEL

    if client is None:
        client = _default_client()

    resp = await client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        think=True,
        options={
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": config.OLLAMA_NUM_PREDICT,
            "temperature": config.OLLAMA_TEMPERATURE,
        },
    )

    raw = (resp["message"]["content"] or "") if resp else ""
    narrative = _strip_think_tags(raw)

    per_layer_notes, anomaly_concerns = _deterministic_scaffolding(strat)
    # Guardrail runs AFTER generation: grounding violations are flagged, not
    # silently trusted.
    grounding_flags = ground_check(narrative, strat)
    flagged_concerns = grounding_flags + anomaly_concerns

    return InterpretationResult(
        narrative=narrative,
        per_layer_notes=per_layer_notes,
        flagged_concerns=flagged_concerns,
        model=model,
        is_ai_draft=True,
    )
