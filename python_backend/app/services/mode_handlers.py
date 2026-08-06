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
from app.services.prompt_config import THREAD_DOC_FALLBACK_PROMPT


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
    return ModeResult(answer=answer, sources=[], no_high_confidence_sources=False)
