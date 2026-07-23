"""Phase 3 unit tests: mode-keyed prompt config, the pure prompt builder, and
the GENERAL handler (no-citation guarantee). All deterministic, no live LLM.
"""

import hashlib

import pytest

from app.services import llm_service, mode_handlers, prompt_config
from app.services.intent_router import GENERAL, KB_QUERY, MIXED, THREAD_DOC, VALID_MODES

pytestmark = pytest.mark.unit


# --- prompt_config: mode coverage + byte-identical KB anchor ----------------
def test_system_prompts_cover_exactly_the_valid_modes():
    assert set(prompt_config.SYSTEM_PROMPTS) == set(VALID_MODES)


def test_get_system_prompt_returns_each_mode():
    for mode in VALID_MODES:
        assert prompt_config.get_system_prompt(mode) == prompt_config.SYSTEM_PROMPTS[mode]


def test_get_system_prompt_unknown_mode_falls_back_to_kb():
    assert prompt_config.get_system_prompt("NOPE") == prompt_config.KB_QUERY_PROMPT


def test_kb_prompt_is_byte_identical_to_pre_router_prompt():
    # Frozen SHA of the exact system prompt previously inlined in
    # generate_answer_with_groq (extracted from git HEAD during Phase 3). If this
    # changes, flag-off behavior is no longer byte-identical -- intentional edits
    # to the KB prompt must update this hash deliberately.
    expected_sha = "d9ea58a0e04c957b1a60b413a40b1dda3ec870cad0091c957630c766c400da90"
    actual = hashlib.sha256(prompt_config.KB_QUERY_PROMPT.encode()).hexdigest()
    assert actual == expected_sha


# --- prompt_config: GENERAL prompt properties -------------------------------
def test_general_prompt_has_no_scope_refusal_or_source_citation():
    p = prompt_config.GENERAL_PROMPT
    # GENERAL must NOT tell the model to refuse non-geotechnical questions...
    assert "politely decline" not in p
    assert "I'm here to help with questions related to geotechnical" not in p
    # ...and must NOT instruct [Source: ...] citations (nothing to cite).
    assert "[Source:" not in p


def test_general_prompt_keeps_shared_output_rules():
    p = prompt_config.GENERAL_PROMPT
    assert "Sources" in p and 'Do NOT add a "Sources"' in p  # no trailing Sources section
    assert "<think>" in p  # retains the no-think instruction
    assert "$...$" in p     # retains the math-delimiter rule


def test_mixed_prompt_demands_explicit_attribution():
    p = prompt_config.MIXED_PROMPT
    assert "ATTRIBUTION" in p
    assert "[Source:" in p  # lab-doc claims are cited


def test_thread_doc_prompt_scopes_to_uploaded_document():
    p = prompt_config.THREAD_DOC_PROMPT
    assert "uploaded" in p.lower()


# --- _build_answer_prompt: KB byte-identical assembly -----------------------
def _expected_kb_prompt(query, context, history):
    """Reconstruct the pre-router full prompt assembly independently."""
    system_prompt = prompt_config.KB_QUERY_PROMPT
    history_text = ""
    if history and len(history) > 0:
        history_text = "\n\nCONVERSATION HISTORY:\n"
        for msg in history:
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")
            history_text += f"{role}: {content}\n"
    if context and context.strip():
        context_section = f"\n\nRELEVANT CONTEXT FROM DOCUMENTS:\n{context}\n"
    else:
        context_section = "\n\n[No relevant documents found in the knowledge base]\n"
    return f"""{system_prompt}
{history_text}
{context_section}

USER QUESTION: {query}

Please provide a detailed answer:"""


@pytest.mark.parametrize(
    "context,history",
    [
        ("[Source: Bolton (1986)]\nSome text about dilatancy.", None),
        ("", None),  # no chunks -> the "[No relevant documents found]" branch
        (
            "[Source: X]\nctx",
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        ),
    ],
)
def test_build_answer_prompt_kb_is_byte_identical(context, history):
    built = llm_service._build_answer_prompt(context=context, query="What is X?", history=history)
    assert built == _expected_kb_prompt("What is X?", context, history)


def test_build_answer_prompt_default_mode_is_kb_query():
    a = llm_service._build_answer_prompt(query="Q", context="ctx")
    b = llm_service._build_answer_prompt(query="Q", context="ctx", mode=KB_QUERY)
    assert a == b


# --- _build_answer_prompt: GENERAL omits the context section ----------------
def test_general_prompt_assembly_omits_context_and_no_docs_line():
    built = llm_service._build_answer_prompt(
        query="Explain liquefaction.", context="", history=None, mode=GENERAL
    )
    # The GENERAL system prompt is used...
    assert prompt_config.GENERAL_PROMPT in built
    # ...and neither a context block nor the misleading "no documents" line appears.
    assert "RELEVANT CONTEXT FROM DOCUMENTS" not in built
    assert "No relevant documents found" not in built
    assert "USER QUESTION: Explain liquefaction." in built


def test_general_prompt_assembly_ignores_any_context_passed():
    # Even if a caller passed context, GENERAL must not embed it.
    built = llm_service._build_answer_prompt(
        query="Q", context="[Source: leak]\nsecret", history=None, mode=GENERAL
    )
    assert "secret" not in built
    assert "[Source: leak]" not in built


# --- GENERAL handler: no citation payload -----------------------------------
@pytest.mark.asyncio
async def test_handle_general_produces_no_sources(monkeypatch):
    captured = {}

    async def fake_generate(*, query, context, history, mode):
        captured.update(query=query, context=context, history=history, mode=mode)
        return "A helpful general answer about soil."

    monkeypatch.setattr(mode_handlers, "generate_answer_with_groq", fake_generate)

    result = await mode_handlers.handle_general(
        "What is a plasticity index?",
        history=[{"role": "user", "content": "earlier"}],
    )

    assert result.answer == "A helpful general answer about soil."
    assert result.sources == []  # <-- no citation payload
    assert result.no_high_confidence_sources is False
    # handler drives the LLM in GENERAL mode with no context
    assert captured["mode"] == GENERAL
    assert captured["context"] == ""
    assert captured["query"] == "What is a plasticity index?"
    assert captured["history"] == [{"role": "user", "content": "earlier"}]


@pytest.mark.asyncio
async def test_handle_general_default_history(monkeypatch):
    async def fake_generate(*, query, context, history, mode):
        return "ans"

    monkeypatch.setattr(mode_handlers, "generate_answer_with_groq", fake_generate)
    result = await mode_handlers.handle_general("hello")
    assert result.sources == []
    assert result.answer == "ans"


# --- THREAD_DOC confidence-fallback prompt + handler ------------------------
def test_thread_doc_fallback_prompt_acknowledges_document():
    p = prompt_config.THREAD_DOC_FALLBACK_PROMPT
    # Must instruct the model NOT to claim the user uploaded nothing ...
    assert "not" in p.lower() and "uploaded a document" in p.lower()
    assert "HAS uploaded" in p
    # ... and to say the answer was not found in the document.
    assert "not found in it" in p or "not be found in it" in p
    # No citations / no <think>, like the other prompts.
    assert 'Do NOT add a "Sources"' in p
    assert "<think>" in p


def test_thread_doc_fallback_prompt_is_not_a_router_mode():
    # It must NOT be in SYSTEM_PROMPTS (keeps keys == VALID_MODES).
    assert prompt_config.THREAD_DOC_FALLBACK_PROMPT not in prompt_config.SYSTEM_PROMPTS.values()


@pytest.mark.asyncio
async def test_handle_thread_doc_fallback_uses_fallback_prompt_and_no_sources(monkeypatch):
    captured = {}

    async def fake_generate(*, query, context, history, mode, system_prompt=None):
        captured.update(query=query, context=context, history=history, mode=mode, system_prompt=system_prompt)
        return "I searched your uploaded document but did not find that. In general, ..."

    monkeypatch.setattr(mode_handlers, "generate_answer_with_groq", fake_generate)
    result = await mode_handlers.handle_thread_doc_fallback("what is the pile capacity?", history=None)

    assert result.sources == []                    # no citations
    assert result.no_high_confidence_sources is False
    # driven with the THREAD_DOC-fallback prompt, no context, GENERAL assembly
    assert captured["system_prompt"] == prompt_config.THREAD_DOC_FALLBACK_PROMPT
    assert captured["context"] == ""
    assert captured["mode"] == GENERAL


def test_thread_doc_fallback_prompt_assembly_omits_context_and_no_docs_line():
    # Built with mode=GENERAL + override -> uses the fallback prompt, and neither
    # a context block nor the "[No relevant documents found]" line appears.
    built = llm_service._build_answer_prompt(
        query="Q", context="", history=None,
        mode=GENERAL, system_prompt=prompt_config.THREAD_DOC_FALLBACK_PROMPT,
    )
    assert prompt_config.THREAD_DOC_FALLBACK_PROMPT in built
    assert "No relevant documents found" not in built
    assert "RELEVANT CONTEXT FROM DOCUMENTS" not in built


def test_system_prompt_override_wins_over_mode():
    built = llm_service._build_answer_prompt(
        query="Q", context="ctx", mode=KB_QUERY, system_prompt="CUSTOM SYSTEM PROMPT"
    )
    assert built.startswith("CUSTOM SYSTEM PROMPT")
    assert prompt_config.KB_QUERY_PROMPT not in built
