"""Per-mode chat handlers.

The intent router (app/services/intent_router.py) classifies each message into a
mode; this module holds the handler that produces the answer for a mode. Each
handler returns a uniform :class:`ModeResult` so the chat path can dispatch on
mode and map the result onto the API response without per-mode branching in the
router file.

Only the GENERAL handler exists so far. KB_QUERY keeps its existing inline path
in chat.py for now; MIXED and THREAD_DOC handlers arrive in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.intent_router import GENERAL
from app.services.llm_service import TokenEmitter, generate_answer_with_groq
from app.services.prompt_config import THREAD_DOC_FALLBACK_PROMPT, kb_fallback_prompt


@dataclass
class ModeResult:
    """Uniform result of handling one chat message in a given mode.

    ``sources`` is the citation payload shown to the user;
    ``no_high_confidence_sources`` mirrors the existing RAGChatResponse field.
    GENERAL always yields an empty ``sources`` (no citations) and False.
    """

    answer: str
    sources: List[Any] = field(default_factory=list)
    no_high_confidence_sources: bool = False


async def handle_general(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    emit: Optional[TokenEmitter] = None,
) -> ModeResult:
    """Answer a GENERAL message from the model's own knowledge.

    No retrieval, no context, and NO citation payload: the GENERAL system prompt
    (prompt_config) answers directly and the returned ``sources`` is always
    empty. This is the whole point of the mode -- general questions, concept
    explanations, writing help, and conversation must not carry document
    citations they were never grounded in.

    ``emit`` is passed straight through to the LLM call: GENERAL is a very common
    mode (and the retrieval-confidence fallback), so it must stream too.
    """
    answer = await generate_answer_with_groq(
        query=query,
        context="",  # no documents in GENERAL mode
        history=history,
        mode=GENERAL,
        emit=emit,
    )
    return ModeResult(answer=answer, sources=[], no_high_confidence_sources=False)


async def handle_thread_doc_fallback(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    emit: Optional[TokenEmitter] = None,
) -> ModeResult:
    """THREAD_DOC confidence fallback: the thread HAS an uploaded document but no
    chunk cleared the reranker threshold for this question.

    Answers WITHOUT citations (like GENERAL) but with a thread-aware system
    prompt that acknowledges the uploaded document exists and states the specific
    answer was not found in it -- so the user is never told they uploaded nothing.
    Reuses GENERAL's no-context prompt assembly (mode=GENERAL) with the
    THREAD_DOC-fallback prompt overriding the system prompt.
    """
    answer = await generate_answer_with_groq(
        query=query,
        context="",  # no confident document context to ground on
        history=history,
        mode=GENERAL,  # -> GENERAL's no-context assembly (no "[No documents]" line)
        system_prompt=THREAD_DOC_FALLBACK_PROMPT,
        emit=emit,
    )
    # This IS the "documents retrieved, none confident" case the field exists
    # to flag: the thread's document was searched and nothing cleared the
    # reranker threshold, so the answer is ungrounded. Surface that.
    return ModeResult(answer=answer, sources=[], no_high_confidence_sources=True)


async def handle_kb_fallback(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    found_titles: List[str],
    emit: Optional[TokenEmitter] = None,
) -> ModeResult:
    """KB_QUERY confidence fallback: retrieval FOUND documents but no chunk
    cleared the reranker threshold for this question.

    Answers WITHOUT citations (like GENERAL) but with a system prompt that
    tells the truth about the search: documents were found — named via
    ``found_titles`` — and simply did not match confidently. It forbids the
    model from claiming it has no access to files (GENERAL's "There are NO
    reference documents" wording is FALSE on this path and produced exactly
    that claim). Callers with an EMPTY title list must use handle_general
    instead — nothing was found, so GENERAL's wording is accurate there.
    """
    answer = await generate_answer_with_groq(
        query=query,
        context="",  # low-confidence chunks are deliberately NOT quoted
        history=history,
        mode=GENERAL,  # -> GENERAL's no-context assembly (no "[No documents]" line)
        system_prompt=kb_fallback_prompt(found_titles),
        emit=emit,
    )
    # Documents WERE found and none cleared the reranker threshold -- exactly
    # the situation no_high_confidence_sources was defined for (models.py).
    # It used to stay False here (parity with the plain-GENERAL fallback this
    # path replaced), which made the field False on every router path and the
    # ungrounded-answer case invisible to the client. handle_general stays
    # False: "no confident sources" is meaningless for a path that never
    # retrieved.
    return ModeResult(answer=answer, sources=[], no_high_confidence_sources=True)
