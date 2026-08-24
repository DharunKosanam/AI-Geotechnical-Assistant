"""Scoped Q&A about a single CPT result.

Answers a free-text question grounded STRICTLY in ONE in-session CPT result
object -- the layers, flags and metadata the calculator produced. This path has
NO RAG, NO vector search and NO knowledge-base retrieval: the only context the
model ever sees is built from the stored result dict here. Nothing is read from
the chat/RAG stack.

The model is reached through the SAME raw ``ollama.AsyncClient(think=False)`` as
the AI interpretation (helpers reused from ``ai_interpret``), and the whole path
is gated behind ``WORKSPACE_ENABLED``. The output is an AI DRAFT for a human
engineer to review.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core import config

# Reuse the interpretation's raw-client factory, think-tag stripper and the
# disabled-flag error so both AI paths behave identically (same client, same
# grounding-as-draft stance). No RAG is imported here.
from app.workspace.interpretation.ai_interpret import (
    WorkspaceDisabledError,
    _default_client,
    _strip_think_tags,
)

# --- System prompt: the scoping contract handed to the model ----------------
SYSTEM_PROMPT = (
    "You are answering questions strictly about THIS CPT sounding result. Use "
    "only the provided layer data and flags. If the question cannot be answered "
    "from this result, say so and suggest what would be needed. Do not invent "
    "values. This is an AI draft for engineer review.\n"
    "Do NOT use <think> tags or any XML tags in your response."
)


def _fmt(value: Any) -> str:
    """Format a numeric field to 2 dp; pass other values through as text."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return "?" if value is None else str(value)


def build_qa_context(payload: Dict[str, Any]) -> str:
    """Deterministic context block built ONLY from the stored result object.

    Contains the layer table (depths, thickness, SBT, mean qc, mean Ic + per-
    layer notes), the flagged-for-review items, GWL, cone area ratio, maximum
    depth and the standard reference. No external data is consulted.
    """
    meta = payload.get("metadata", {}) or {}
    layers = payload.get("layers", []) or []
    flags = payload.get("flagged_concerns", []) or []

    lines = [
        f"CPT SOUNDING RESULT (source file: {payload.get('source_file', '?')})",
        f"Standard / method: {payload.get('reference', '')}",
        (
            f"Groundwater level: {_fmt(meta.get('groundwater_level'))} m; "
            f"cone area ratio a = {_fmt(meta.get('area_ratio'))} "
            f"({meta.get('area_ratio_source', '?')}); "
            f"maximum depth: {_fmt(meta.get('max_depth'))} m."
        ),
        "",
        "LAYERS (top to bottom):",
    ]
    for ly in layers:
        lines.append(
            f"Layer {ly.get('layer')}: {_fmt(ly.get('depth_from'))}-"
            f"{_fmt(ly.get('depth_to'))} m (thickness {_fmt(ly.get('thickness'))} m) "
            f"- {ly.get('soil_type', '?')} [SBTn zone {ly.get('sbt_zone')}]; "
            f"mean qc {_fmt(ly.get('qc_mean'))} MPa; mean Ic {_fmt(ly.get('ic_mean'))}."
        )
        for note in ly.get("notes", []) or []:
            lines.append(f"    note: {note}")

    lines.append("")
    if flags:
        lines.append("FLAGGED FOR REVIEW:")
        lines.extend(f"- {f}" for f in flags)
    else:
        lines.append("FLAGGED FOR REVIEW: none.")
    return "\n".join(lines)


async def answer_question(
    question: str,
    payload: Dict[str, Any],
    *,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Answer a question scoped to a single CPT result. AI draft, no RAG.

    Gated behind ``WORKSPACE_ENABLED``. ``client`` may be injected (an object
    exposing an async ``chat``) for testing; in production the shared raw
    ``ollama.AsyncClient`` is built and called with ``think=False``.
    """
    if not config.WORKSPACE_ENABLED:
        raise WorkspaceDisabledError(
            "Workspace Q&A is disabled. Set WORKSPACE_ENABLED to enable it."
        )

    context = build_qa_context(payload)
    user_prompt = (
        f"{context}\n\n"
        f"Question: {question.strip()}\n\n"
        "Answer using only the data above."
    )
    model = config.OLLAMA_MODEL

    if client is None:
        client = _default_client()

    resp = await client.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
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
    return {
        "answer": _strip_think_tags(raw),
        "is_ai_draft": True,
        "model": model,
    }
