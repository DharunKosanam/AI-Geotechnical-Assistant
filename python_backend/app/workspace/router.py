"""LLM intent router for the GeoPilot workspace chat.

Decides which registered calculator (if any) a user message is asking to run.
Fixed trigger phrases miss natural phrasing ("run the cpt test", "interpret
this sounding"), so routing must UNDERSTAND intent:

  1. Fast pre-check -- if an exact known trigger phrase is clearly present, route
     directly and SKIP the LLM (saves latency on the common case).
  2. Otherwise ask the LLM (same ``ollama.AsyncClient(think=False)`` as the
     interpretation) to return ONLY ``{"calculator_id": "<id>" | null}``.

The LLM ONLY selects which registered calculator to run; it NEVER performs the
calculation and can only return a KNOWN id or null. The choice is parsed
defensively and validated against the registry -- any doubt resolves to None.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.core import config
from app.workspace.calculators import registry
from app.workspace.calculators.base import Calculator

# Reuse the interpretation's raw-client factory and think-tag stripper so the
# router uses the exact same Ollama client. No RAG is involved.
from app.workspace.interpretation.ai_interpret import _default_client, _strip_think_tags

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = (
    "You are a router. Given a user message and a list of available engineering "
    "calculators, decide which ONE the user is asking to run. Return ONLY JSON "
    '{"calculator_id": "<id>"} using an id from the list, or '
    '{"calculator_id": null} if the message is a question or does not clearly '
    "ask to run any calculator. Do not run or explain anything. Do not invent ids."
)


def _catalog() -> str:
    """The id + one-line description of each registered calculator."""
    return "\n".join(
        f"- id: {c.id} | {c.description}" for c in registry.all_calculators()
    )


def _parse_calculator_id(raw: str) -> Optional[str]:
    """Extract a valid calculator id from the model output, else None.

    Defensive: strips ``<think>`` blocks and code fences, isolates the first
    JSON object, and validates the id against the registry. Any parse failure,
    non-dict payload, null, or unknown id -> None.
    """
    text = _strip_think_tags(raw or "").strip()
    # Drop ```json ... ``` fences if present.
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    # Isolate the first {...} block so trailing prose does not break json.loads.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    cid = data.get("calculator_id")
    if not isinstance(cid, str):
        return None  # null / missing / non-string -> no calculator
    return cid if registry.get_calculator(cid) is not None else None


async def route_message(
    message: str, *, client: Optional[Any] = None
) -> Optional[Calculator]:
    """Return the calculator the message asks to run, or None.

    Never raises for routing reasons: if the LLM is unreachable or returns
    anything unparseable, the message is treated as "no calculator" (None) so
    the caller falls through to Q&A / info exactly as for an explicit null.
    """
    # 1. Fast pre-check: exact known trigger phrase present -> no LLM call.
    fast = registry.match_calculator(message)
    if fast is not None:
        logger.info("router decision: %r -> %s [fast-path]", message, fast.id)
        return fast

    # 2. LLM router. Belt-and-suspenders flag check (route is already gated).
    if not config.WORKSPACE_ENABLED:
        return None
    if client is None:
        client = _default_client()

    user_prompt = (
        f"Available calculators:\n{_catalog()}\n\n"
        f"User message: {message.strip()}\n\n"
        'Respond with ONLY {"calculator_id": "<id>"} or {"calculator_id": null}.'
    )
    try:
        resp = await client.chat(
            model=config.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            think=False,
            format="json",  # ask Ollama to constrain output to a JSON object
            options={
                "num_ctx": config.OLLAMA_NUM_CTX,
                "num_predict": 64,
                "temperature": 0.0,
            },
        )
    except Exception as exc:  # noqa: BLE001 - unreachable model -> treat as null
        # A silent fall-through to null (info branch) is exactly the bug that
        # hides a down/misconfigured model, so make it loud.
        logger.warning("router decision: %r -> null [LLM call FAILED: %s]", message, exc)
        return None

    raw = (resp["message"]["content"] or "") if resp else ""
    cid = _parse_calculator_id(raw)
    calc = registry.get_calculator(cid) if cid else None
    logger.info(
        "router decision: %r -> %s [llm; raw=%r]",
        message,
        calc.id if calc else None,
        raw,
    )
    return calc
