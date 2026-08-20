"""AI DRAFT interpretation for dataset-bound calculator results.

Same stance and same client as the CPT interpretation: the deterministic
result is computed first by pure Python; this hook only turns its numbers
into a short plain-English draft that is CLEARLY labelled as an AI draft for
engineer review. It reuses ``ai_interpret``'s raw ``ollama.AsyncClient``
(``think=False``), think-tag stripper and disabled-flag error. No RAG.

The prompt embeds ONLY the compact ``raw`` dict the calculator returned, and
the draft is instructed not to invent numbers; the caller renders it collapsed
behind an explicit review affordance.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.core import config
from app.workspace.interpretation.ai_interpret import (
    WorkspaceDisabledError,
    _default_client,
    _strip_think_tags,
)

SYSTEM_PROMPT = (
    "You are a geotechnical/pavement instrumentation engineer's assistant. You are "
    "given the DETERMINISTIC numeric results of a calculation that has already been "
    "run on instrument data. Write a short plain-English draft (3-6 sentences) that "
    "restates the key numbers and what they suggest, for a supervising engineer to "
    "review. Use ONLY the numbers given; do not invent values, units or standards. "
    "If a status note says a method is provisional or pending validation, say so "
    "explicitly and do not describe the counts as validated. Do NOT use <think> tags "
    "or any XML tags. Plain prose, no headings."
)


def build_prompt(raw: Dict[str, Any]) -> str:
    return (
        "Deterministic results (JSON):\n"
        + json.dumps(raw, indent=1, default=str)
        + "\n\nDraft interpretation:"
    )


async def interpret_dataset_result(
    raw: Any, *, client: Optional[Any] = None
) -> Dict[str, Any]:
    """Return ``{narrative, per_layer_notes, flagged_concerns, is_ai_draft, model}``
    (the same shape the CPT hook returns so the route/UI need no special case)."""
    if not config.WORKSPACE_ENABLED:
        raise WorkspaceDisabledError(
            "AI Interpretation is disabled. Set WORKSPACE_ENABLED to enable it."
        )
    payload = raw if isinstance(raw, dict) else {"result": str(raw)}
    model = config.OLLAMA_MODEL
    if client is None:
        client = _default_client()
    resp = await client.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(payload)},
        ],
        think=False,
        options={
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": min(int(config.OLLAMA_NUM_PREDICT), 700),
            "temperature": config.OLLAMA_TEMPERATURE,
        },
    )
    text = (resp["message"]["content"] or "") if resp else ""
    narrative = _strip_think_tags(text)
    concerns = []
    for note in payload.get("notes", []) if isinstance(payload, dict) else []:
        concerns.append(str(note))
    return {
        "narrative": narrative,
        "per_layer_notes": [],
        "flagged_concerns": concerns,
        "is_ai_draft": True,
        "model": model,
    }
