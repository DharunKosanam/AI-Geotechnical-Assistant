"""LLM intent router for the Chat tab.

Classifies each incoming chat message into ONE of four handling modes so the
chat path can stop retrieving unconditionally and instead answer general
questions from the model's own knowledge while keeping KB retrieval for the
questions that need it.

Modes
-----
KB_QUERY   -- about the lab's indexed documents / shared reference material.
              Runs the existing retrieval path; returns citations.
GENERAL    -- domain knowledge, concept explanation, writing help, or plain
              conversation. Skips retrieval; answers from the LLM alone; no
              citations.
MIXED      -- needs BOTH the knowledge base AND general knowledge. Retrieves,
              then blends; citations cover only the retrieved portion.
THREAD_DOC -- about a document the user uploaded into THIS thread. Retrieval is
              scoped to that thread's documents only. Only valid when the thread
              actually HAS uploaded documents.
INVENTORY  -- (INVENTORY_ENABLED only) about the lab's live inventory state.
              Skips retrieval AND reranking; answered from the deterministic
              inventory snapshot.

Design (mirrors app/workspace/router.py, the proven in-repo pattern):
  * The LLM ONLY selects a mode from a fixed set; it never answers or retrieves.
  * The message is reached through the SAME raw ``ollama.AsyncClient`` (called
    with ``think=False``) the rest of the app uses -- NOT the llama-index
    wrapper, which drops the ``think`` flag. Base URL / model / num_ctx come
    from config, so the router is model-agnostic (nothing is hardcoded to any
    specific Ollama model).
  * The output is parsed defensively and validated against the known modes; any
    doubt resolves to ``DEFAULT_MODE`` (KB_QUERY) so a router failure degrades
    to exactly today's always-retrieve behavior rather than breaking the chat.
  * Hard invariant enforced in CODE (not just the prompt): a thread with no
    uploaded documents can NEVER route to THREAD_DOC.

This module is standalone: it performs no retrieval and is not yet wired into
the chat path (that happens in a later phase, behind ROUTER_ENABLED).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import ollama

from app.core import config

logger = logging.getLogger(__name__)


# --- Shared Ollama wiring ---------------------------------------------------
# Local copies of the raw-client factory + think-tag stripper used across the
# app (see app/services/llm_service.py and app/workspace). Defined here rather
# than imported from the workspace package so the chat path never couples to
# workspace at import time. Behavior is identical: the raw ``ollama.AsyncClient``
# honours ``think=False`` (the llama-index wrapper does not), and the stripper
# is a defensive no-op for models that never emit <think> tags.
def _default_client() -> "ollama.AsyncClient":
    """Construct the shared raw Ollama async client (honours think=False)."""
    return ollama.AsyncClient(
        host=config.OLLAMA_BASE_URL, timeout=config.OLLAMA_REQUEST_TIMEOUT
    )


def _strip_think_tags(text: str) -> str:
    """Defensively remove any <think>...</think> block (should not occur with
    think=False, but the guard is cheap)."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


# --- Modes ------------------------------------------------------------------
KB_QUERY = "KB_QUERY"
GENERAL = "GENERAL"
MIXED = "MIXED"
THREAD_DOC = "THREAD_DOC"
# Lab-inventory questions (INVENTORY_ENABLED): what the lab owns, who has an
# item, availability/overdue/stock/maintenance, reservations, PLAXIS seats,
# bookability. Answered from the deterministic inventory snapshot — retrieval
# and reranking are skipped entirely (the instrument-parsers routing-away
# principle). In VALID_MODES because it IS a dispatchable mode (unlike
# UNCERTAIN below), but _parse_mode accepts it only while the flag is on, so
# a flag-off deployment can never see it.
INVENTORY = "INVENTORY"

# Every valid mode. Anything outside this set from the model is rejected.
VALID_MODES = frozenset({KB_QUERY, GENERAL, MIXED, THREAD_DOC, INVENTORY})

# Uncertainty class (ROUTER_UNCERTAIN_RETRIEVES only). Deliberately NOT in
# VALID_MODES — it never leaves this module: classify() resolves it to
# KB_QUERY so uncertainty fails toward retrieval and the reranker threshold
# decides. The router's decision is a bare JSON label with no confidence or
# logprobs, so this explicit class is the only way the model can say
# "I don't know" instead of guessing GENERAL and skipping retrieval.
UNCERTAIN = "UNCERTAIN"

# Safe fallback. KB_QUERY reproduces today's unconditional-retrieval behavior,
# so a router failure (unreachable model, unparseable output) degrades to the
# current path rather than dropping retrieval. The downstream retrieval-
# confidence fallback (later phase) still lets a KB_QUERY with no good chunk
# fall through to a general answer, so this default never returns "not found".
DEFAULT_MODE = KB_QUERY


ROUTER_SYSTEM_PROMPT = (
    "You are an intent classifier for a geotechnical engineering assistant. "
    "Read the user's LATEST MESSAGE, using the CONVERSATION HISTORY for context, "
    "and choose exactly ONE mode describing how the message should be answered. "
    'Return ONLY JSON of the form {"mode": "<MODE>"} and nothing else.\n\n'
    "Modes:\n"
    "- KB_QUERY: asks about the lab's documents, indexed research papers, "
    "project reports, or shared reference material. Answered from the document "
    "knowledge base with citations.\n"
    "- GENERAL: a general geotechnical or engineering question, a request to "
    "explain a concept, help with writing or drafting, or ordinary "
    "conversation. Answered from your own knowledge, no documents.\n"
    "- MIXED: needs BOTH the knowledge base AND general knowledge (for example, "
    "comparing what a lab report says with standard engineering practice).\n"
    "- THREAD_DOC: about a document the user uploaded into THIS conversation. "
    "Only valid when this thread HAS an uploaded document.\n\n"
    "Rules:\n"
    '1. Output ONLY {"mode": "<MODE>"} using one of KB_QUERY, GENERAL, MIXED, '
    "THREAD_DOC. No prose, no explanation, no extra keys.\n"
    '2. If "THIS THREAD HAS UPLOADED DOCUMENTS" is "no", you MUST NOT choose '
    "THREAD_DOC.\n"
    "3. A short follow-up or acknowledgement (\"go on\", \"more detail\", "
    "\"why?\", \"ok\") inherits the topic and intent of the prior turns in the "
    "history.\n"
    "4. When torn between KB_QUERY and GENERAL, choose KB_QUERY only if the "
    "message plausibly refers to specific documents, reports, or indexed "
    "material; otherwise choose GENERAL."
)


def _system_prompt() -> str:
    """The classifier system prompt, resolved at CALL time.

    Flag-gated rules are appended after the base prompt's rule 4, numbered
    sequentially; with every flag off the prompt is byte-identical to before
    any of these features existed.

    * WEB_INGEST_ENABLED: the KB also holds captured university information
      pages (travel/conference funding, awards) and questions about those
      topics must reach retrieval — the base "lab documents / research
      papers" framing routes them to GENERAL, ungrounded and uncited.
    * ROUTER_UNCERTAIN_RETRIEVES: offers the explicit UNCERTAIN class so an
      unsure model stops guessing GENERAL (which skips retrieval outright);
      classify() resolves UNCERTAIN to KB_QUERY.
    * INVENTORY_ENABLED: offers the INVENTORY mode for live lab-inventory
      state (who has what, availability, bookings, PLAXIS seats); those turns
      are answered from a deterministic snapshot, never retrieval."""
    extras = []
    if config.WEB_INGEST_ENABLED:
        extras.append(
            "The knowledge base ALSO contains captured university information "
            "pages (for example UVic travel and conference funding, award "
            "eligibility, application deadlines). A question about funding, "
            "awards, deadlines or university procedures is KB_QUERY."
        )
    if config.ROUTER_UNCERTAIN_RETRIEVES:
        extras.append(
            'You may also output {"mode": "UNCERTAIN"} when you cannot '
            "confidently tell whether the lab's documents would answer the "
            "question. Prefer UNCERTAIN over GENERAL whenever the question "
            "asks for specific facts, measurements, observations, readings or "
            "records that project documents COULD hold — even if it does not "
            "mention a document. Reserve GENERAL for clearly general concept "
            "explanations, definitions, writing help and conversation."
        )
    if config.INVENTORY_ENABLED:
        extras.append(
            'You may also output {"mode": "INVENTORY"} for questions about '
            "the lab's CURRENT inventory state: what equipment, consumables "
            "or software the lab owns; who has an item checked out; what is "
            "available right now; overdue loans; stock levels; maintenance or "
            "calibration due; reservations; PLAXIS seats; or whether a set of "
            "equipment can be booked for a time window. These are answered "
            "from the live inventory system, not documents. A question that "
            "needs BOTH lab documents AND inventory state stays MIXED."
        )
    prompt = ROUTER_SYSTEM_PROMPT
    for i, body in enumerate(extras, start=5):
        prompt += f"\n{i}. {body}"
    return prompt


def _history_block(history: Optional[List[Dict[str, str]]], max_turns: int = 4) -> str:
    """Render the last ``max_turns`` conversation turns for the classifier.

    Only the most recent turns are needed to resolve a follow-up; older context
    does not change the mode and only costs tokens. Returns a placeholder when
    there is no prior conversation.
    """
    recent = (history or [])[-max_turns:]
    if not recent:
        return "(no prior conversation)"
    return "\n".join(
        f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in recent
    )


def _build_user_prompt(
    message: str,
    history: Optional[List[Dict[str, str]]],
    thread_has_attachments: bool,
) -> str:
    """Assemble the classifier user prompt.

    The attachment flag is stated explicitly so the model does not pick
    THREAD_DOC when the thread has no uploads (rule 2). It is ALSO enforced in
    code after parsing, so a model that ignores the rule still cannot leak
    THREAD_DOC.
    """
    return (
        f"THIS THREAD HAS UPLOADED DOCUMENTS: {'yes' if thread_has_attachments else 'no'}\n\n"
        f"CONVERSATION HISTORY:\n{_history_block(history)}\n\n"
        f"LATEST MESSAGE: {message.strip()}\n\n"
        'Respond with ONLY {"mode": "<MODE>"}.'
    )


def _parse_mode(raw: str) -> Optional[str]:
    """Extract a valid mode from the model output, else None.

    Defensive, mirroring workspace.router._parse_calculator_id: strips
    ``<think>`` blocks and code fences, isolates the first JSON object, and
    validates the ``mode`` value (upper-cased) against ``VALID_MODES``. Any
    parse failure, non-dict payload, missing/non-string mode, or unknown mode
    returns None.
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
    mode = data.get("mode")
    if not isinstance(mode, str):
        return None
    mode = mode.strip().upper()
    # UNCERTAIN is accepted ONLY when the flag offers it in the prompt; with
    # the flag off it stays invalid (-> None -> DEFAULT_MODE), exactly as any
    # unknown label today.
    if mode == UNCERTAIN and config.ROUTER_UNCERTAIN_RETRIEVES:
        return UNCERTAIN
    # INVENTORY is in VALID_MODES (it is a real dispatchable mode) but is
    # accepted ONLY while its flag offers it in the prompt — a flag-off
    # deployment treats the label as unknown (-> None -> DEFAULT_MODE), so
    # flag-off routing is byte-identical to before the feature.
    if mode == INVENTORY and not config.INVENTORY_ENABLED:
        return None
    return mode if mode in VALID_MODES else None


def _enforce_attachment_invariant(mode: str, thread_has_attachments: bool) -> str:
    """A thread with no uploads can never be THREAD_DOC.

    If the model chose THREAD_DOC but the thread has no attachments, downgrade
    to KB_QUERY: the user asked about "the document" that does not exist here,
    so search the shared index (the retrieval-confidence fallback will drop to a
    general answer if nothing matches). Logged so the downgrade is visible.
    """
    if mode == THREAD_DOC and not thread_has_attachments:
        logger.info(
            "router: downgrading THREAD_DOC -> KB_QUERY (thread has no attachments)"
        )
        return KB_QUERY
    return mode


async def classify(
    message: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
    thread_has_attachments: bool = False,
    client: Optional[Any] = None,
) -> str:
    """Classify ``message`` into one of ``VALID_MODES``.

    Never raises for routing reasons: if the LLM is unreachable or returns
    anything unparseable, the message is classified as ``DEFAULT_MODE``
    (KB_QUERY) so the caller falls through to today's retrieval path.

    ``client`` may be injected (an object exposing an async ``chat``) for
    testing; in production the shared raw ``ollama.AsyncClient`` is built and
    called with ``think=False``.
    """
    if client is None:
        client = _default_client()

    user_prompt = _build_user_prompt(message, history, thread_has_attachments)

    try:
        resp = await client.chat(
            model=config.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": _system_prompt()},
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
    except Exception as exc:  # noqa: BLE001 - unreachable model -> safe default
        # Loud, not silent: a down/misconfigured model must not masquerade as a
        # confident classification. Degrade to today's retrieval path.
        logger.warning(
            "router decision: %r -> %s [LLM call FAILED: %s]",
            message,
            DEFAULT_MODE,
            exc,
        )
        return DEFAULT_MODE

    raw = (resp["message"]["content"] or "") if resp else ""
    parsed = _parse_mode(raw)
    if parsed == UNCERTAIN:
        # Uncertainty fails TOWARD retrieval: run the KB path and let the
        # reranker threshold (and the honest fallback) decide, instead of the
        # classifier skipping retrieval on a guess.
        logger.info("router uncertain -> KB_QUERY (retrieval decides): %r", message)
        parsed = KB_QUERY
    mode = parsed if parsed is not None else DEFAULT_MODE
    mode = _enforce_attachment_invariant(mode, thread_has_attachments)

    logger.info(
        "router decision: %r (attachments=%s) -> %s [raw=%r]",
        message,
        thread_has_attachments,
        mode,
        raw,
    )
    return mode


# --- THREAD_DOC granularity: document-level vs content-level -----------------
# A THREAD_DOC message is either about the document AS A WHOLE (summarize,
# what is this, main findings, overview) or about SPECIFIC CONTENT within it.
# The distinction matters because relevance ranking is meaningless for
# document-level requests: a "summarize this" query shares no content terms
# with any chunk, the cross-encoder scores every chunk at its floor, and the
# turn falls through to the low-confidence fallback (measured on the incident
# document: all 6 chunks between -11.23 and -11.47 against the -11.0
# threshold). Document-level requests skip ranking and read a structured
# sample instead. Classified by the same LLM as the mode router -- keyword
# lists are what the router replaced.
DOC_LEVEL = "DOC_LEVEL"
CONTENT = "CONTENT"
_VALID_SCOPES = frozenset({DOC_LEVEL, CONTENT})

# Safe fallback: CONTENT reproduces the existing ranked-retrieval behavior, so
# a classifier failure degrades to today's path rather than mis-sampling.
DEFAULT_SCOPE = CONTENT

SCOPE_SYSTEM_PROMPT = (
    "You are an intent classifier for an assistant answering questions about "
    "a document the user uploaded into this conversation. Read the user's "
    "LATEST MESSAGE, using the CONVERSATION HISTORY for context, and decide "
    "whether it is about the document AS A WHOLE or about SPECIFIC CONTENT "
    'inside it. Return ONLY JSON of the form {"scope": "<SCOPE>"} and '
    "nothing else.\n\n"
    "Scopes:\n"
    "- DOC_LEVEL: the request is about the document as a whole -- summarize "
    "it, what is this document, what is it about, main findings, key points, "
    "overview, general impressions, table of contents.\n"
    "- CONTENT: the request is about specific information, terms, values, "
    "sections, or claims within the document -- anything answerable by "
    "finding particular passages.\n\n"
    "Rules:\n"
    '1. Output ONLY {"scope": "<SCOPE>"} using DOC_LEVEL or CONTENT. No '
    "prose, no explanation, no extra keys.\n"
    "2. A short follow-up (\"go on\", \"and the rest?\") inherits the scope "
    "of the prior turns in the history.\n"
    "3. When torn, choose CONTENT."
)


def _parse_scope(raw: str) -> Optional[str]:
    """Extract a valid scope from the model output, else None. Same defensive
    parse as _parse_mode."""
    text = _strip_think_tags(raw or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    scope = data.get("scope")
    if not isinstance(scope, str):
        return None
    scope = scope.strip().upper()
    return scope if scope in _VALID_SCOPES else None


async def classify_thread_doc_scope(
    message: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
    client: Optional[Any] = None,
) -> str:
    """Classify a THREAD_DOC message as DOC_LEVEL or CONTENT.

    Called only after the main router chose THREAD_DOC (router-on path), so it
    adds one small LLM call to thread-document turns only. Never raises: any
    failure returns DEFAULT_SCOPE (CONTENT), which is the existing ranked
    behavior.
    """
    if client is None:
        client = _default_client()

    user_prompt = (
        f"CONVERSATION HISTORY:\n{_history_block(history)}\n\n"
        f"LATEST MESSAGE: {message.strip()}\n\n"
        'Respond with ONLY {"scope": "<SCOPE>"}.'
    )

    try:
        resp = await client.chat(
            model=config.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SCOPE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            think=False,
            format="json",
            options={
                "num_ctx": config.OLLAMA_NUM_CTX,
                "num_predict": 64,
                "temperature": 0.0,
            },
        )
    except Exception as exc:  # noqa: BLE001 - unreachable model -> safe default
        logger.warning(
            "doc-scope decision: %r -> %s [LLM call FAILED: %s]",
            message, DEFAULT_SCOPE, exc,
        )
        return DEFAULT_SCOPE

    raw = (resp["message"]["content"] or "") if resp else ""
    parsed = _parse_scope(raw)
    scope = parsed if parsed is not None else DEFAULT_SCOPE
    logger.info("doc-scope decision: %r -> %s [raw=%r]", message, scope, raw)
    return scope
