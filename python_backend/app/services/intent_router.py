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

# Every valid mode. Anything outside this set from the model is rejected.
VALID_MODES = frozenset({KB_QUERY, GENERAL, MIXED, THREAD_DOC})

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
