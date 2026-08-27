"""
LLM service for managing Groq AI interactions
"""
import os
import re
import sys
import time
from typing import Awaitable, Callable, List, Dict, Optional
import httpx
import ollama
from llama_index.llms.groq import Groq
from dotenv import load_dotenv

from app.core import config
from app.core.config import (
    GROQ_MODEL,
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_NUM_CTX,
    OLLAMA_NUM_PREDICT,
    OLLAMA_REQUEST_TIMEOUT,
    OLLAMA_REWRITE_TIMEOUT,
    OLLAMA_TEMPERATURE,
)
from app.services.intent_router import GENERAL, INVENTORY, KB_QUERY
from app.services.prompt_config import get_system_prompt

# Load environment variables
load_dotenv()


def safe_print(message: str) -> None:
    """print() that never crashes the request path on a non-UTF-8 console.

    On Windows the redirected stdout codec is often cp1252, which raises
    UnicodeEncodeError when a debug line interpolates a non-English query (e.g. a
    Chinese question) or a Greek symbol (γ, σ). We fall back to backslash escapes
    so logging degrades gracefully instead of taking down the chat request.
    """
    try:
        print(message)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(message.encode(enc, errors="backslashreplace").decode(enc))


def get_llm():
    """
    Initialize and return the configured answer LLM instance.

    Provider is chosen by LLM_PROVIDER ("groq" default, or "ollama"). Both
    branches return a llama-index LLM exposing the same .acomplete() interface,
    so the entire downstream RAG pipeline is provider-agnostic.

    Returns:
        A Groq or Ollama llama-index LLM instance.

    Raises:
        ValueError: If LLM_PROVIDER == "groq" and GROQ_API_KEY is not set.
    """
    if LLM_PROVIDER == "ollama":
        # Imported lazily so a Groq-only deployment doesn't need the package.
        from llama_index.llms.ollama import Ollama

        # thinking=True is REQUIRED for qwen3 (not merely tolerated). Qwen3's
        # chat template prefills the opening <think> tag on the prompt side, so
        # with think=False the model still reasons -- but emits that reasoning
        # as plain text into message.content, terminated by a bare closing
        # </think> with no opening tag for the scrubber to pair it with, and the
        # chain-of-thought leaks to the user. With think=True Ollama parses the
        # reasoning into a separate message.thinking field and message.content
        # arrives clean (verified 2026-08-24 on qwen3:30b-a3b: content='PONG',
        # thinking populated; in streaming mode reasoning chunks carry
        # content="" with the text in `thinking`). The wrapper sends this as
        # the ollama API's top-level `think` flag on every request. (NOT
        # additional_kwargs -- that goes into model `options`, not the think
        # flag; and a Modelfile PARAMETER does not work on this Ollama version.)
        # The raw ollama.AsyncClient.chat() classifier call sites pass
        # think=False; the ANSWER path follows OLLAMA_THINK_ANSWERS (default
        # off since 2026-08-26 -- see _think_for_answers and config.py).
        return Ollama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            request_timeout=120.0,
            thinking=True,
        )

    groq_api_key = os.getenv("GROQ_API_KEY")

    if not groq_api_key:
        raise ValueError(
            "GROQ_API_KEY is not set in environment variables. "
            "Please add GROQ_API_KEY to your .env file."
        )

    llm = Groq(
        model=GROQ_MODEL,
        api_key=groq_api_key,
        temperature=0.3,
        max_tokens=4096,
        request_timeout=60.0,
    )

    return llm


def _model_emits_thinking_tags(model_name: str) -> bool:
    """Only qwen3 models wrap their chain-of-thought in <think>...</think>."""
    return "qwen" in (model_name or "").lower()


def _active_model_emits_thinking_tags() -> bool:
    """Whether the currently-configured provider/model emits <think> tags.

    Ollama here serves qwen3.5, which always wraps its chain-of-thought in
    <think>...</think>, so we always route Ollama output through the stripper.
    For Groq it depends on the model name (only qwen* emit them)."""
    if LLM_PROVIDER == "ollama":
        return True
    return _model_emits_thinking_tags(GROQ_MODEL)


def _think_for_answers() -> bool:
    """``think=`` for the answer-path chat calls (initial and guard retry,
    streaming and non-streaming). Read from the config module at CALL time,
    not import time, so OLLAMA_THINK_ANSWERS can be toggled per test / per
    process without re-importing this module. Default off: see config.py.
    """
    return bool(config.OLLAMA_THINK_ANSWERS)


def _ollama_options() -> dict:
    """Generation options for the raw ollama.AsyncClient.chat calls.

    num_ctx is the critical one: the runtime default (4096) is smaller than a
    multi-turn RAG prompt (~7.3k tokens), which starves the output budget and
    truncates answers to a single word. See config.py for the full rationale.
    """
    return {
        "num_ctx": OLLAMA_NUM_CTX,
        "num_predict": OLLAMA_NUM_PREDICT,
        "temperature": OLLAMA_TEMPERATURE,
    }


def _build_answer_prompt(
    query: str,
    context: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    mode: str = KB_QUERY,
    system_prompt: Optional[str] = None,
) -> str:
    """Assemble the full answer prompt (system + history + context + question).

    Pure and side-effect free so it can be unit-tested without an LLM. The
    system prompt is ``system_prompt`` when provided (e.g. the THREAD_DOC
    confidence-fallback prompt), otherwise it is looked up by ``mode`` from
    prompt_config; the context section is OMITTED entirely for GENERAL (no
    retrieval), and otherwise keeps the exact assembly used before the router was
    introduced. For mode=KB_QUERY with no override the returned string is
    byte-identical to the pre-router prompt.
    """
    resolved_prompt = system_prompt if system_prompt is not None else get_system_prompt(mode)

    # Format conversation history if provided. The caller (chat.py) already
    # caps this list (last 20 turns, 6000-token budget), so include all of it
    # rather than re-truncating to the last few messages.
    history_text = ""
    if history and len(history) > 0:
        history_text = "\n\nCONVERSATION HISTORY:\n"
        for msg in history:
            role = msg.get('role', 'user').upper()
            content = msg.get('content', '')
            history_text += f"{role}: {content}\n"

    # Format context section. GENERAL answers from the model's own knowledge with
    # no documents, so there is no context block at all (and no misleading "no
    # documents found" line). INVENTORY's context is the deterministic snapshot,
    # not documents, so it gets its own header (mode unreachable with the flag
    # off, so every other mode keeps the original behavior byte-identically).
    if mode == GENERAL:
        context_section = ""
    elif mode == INVENTORY:
        if context and context.strip():
            context_section = f"\n\nLIVE INVENTORY SNAPSHOT:\n{context}\n"
        else:
            context_section = "\n\n[The inventory system returned no data]\n"
    elif context and context.strip():
        context_section = f"\n\nRELEVANT CONTEXT FROM DOCUMENTS:\n{context}\n"
    else:
        context_section = "\n\n[No relevant documents found in the knowledge base]\n"

    # Build the complete prompt
    return f"""{resolved_prompt}
{history_text}
{context_section}

USER QUESTION: {query}

Please provide a detailed answer:"""


# A token emitter: called with each newly-final piece of answer text as it is
# produced. Supplied only by the streaming chat path; None everywhere else.
TokenEmitter = Callable[[str], Awaitable[None]]

# A healthy generation is hundreds-to-thousands of chars. Below this the model
# halted almost immediately (historically num_ctx overflow eating the output
# budget) and we retry once. Streaming holds this many characters back before
# emitting anything, so the retry is still possible — you cannot un-send a token.
SHORT_ANSWER_THRESHOLD = 20

# Number of Ollama answer generations currently awaiting a response (streaming
# and non-streaming, initial and guard retry). Read ONLY by the short-answer
# guard diagnostics below, to answer "was the GPU serving something else at
# the time?" after an empty generation. Single-process counter (uvicorn runs
# one worker here); it is not a limiter and never blocks anything.
_GENERATIONS_IN_FLIGHT = 0


def _resp_field(resp, key):
    """Read one field off an ollama ChatResponse / stream chunk (or a plain
    dict, as the unit-test fakes use); None when absent or unreadable."""
    if resp is None:
        return None
    try:
        getter = getattr(resp, "get", None)
        if callable(getter):
            return getter(key)
    except Exception:
        pass
    return getattr(resp, key, None)


def _log_short_answer_diagnostics(
    resp,
    *,
    attempt: str,
    mode: str,
    streaming: bool,
    prompt: str,
    wall_s: float,
    in_flight: int,
    thinking_chars: Optional[int] = None,
) -> None:
    """One ``[GUARD] diag:`` line explaining a short/empty raw answer.

    Added 2026-08-26 after Ollama returned '' twice in a row (initial + retry)
    for a KB_QUERY with 4 grounded sources, at 19:40:56, once, unreproducible
    -- CAUSE UNKNOWN. This records what the response itself said (done_reason,
    token counts, durations) plus the call's context so a second occurrence
    is explicable. Failure path only: never printed for a healthy answer.

    ``prompt_eval_count`` is the prompt tokens Ollama actually evaluated --
    a KV-cached prefix is excluded, so it can be far below the prompt's true
    token count; ``prompt_chars`` is the unconditional size. ``resp`` is the
    final stream chunk (streaming) or the ChatResponse (non-streaming).
    """
    def _ms(ns):
        return None if ns is None else round(ns / 1e6)

    if thinking_chars is None:
        message = _resp_field(resp, "message")
        thinking_chars = len(_resp_field(message, "thinking") or "")
    print(
        "   [GUARD] diag: "
        f"attempt={attempt} mode={mode} streaming={streaming} "
        f"done={_resp_field(resp, 'done')!r} "
        f"done_reason={_resp_field(resp, 'done_reason')!r} "
        f"prompt_eval_count={_resp_field(resp, 'prompt_eval_count')} "
        f"eval_count={_resp_field(resp, 'eval_count')} "
        f"prompt_chars={len(prompt)} "
        f"thinking_chars={thinking_chars} "
        f"wall={wall_s:.2f}s "
        f"total_duration_ms={_ms(_resp_field(resp, 'total_duration'))} "
        f"load_duration_ms={_ms(_resp_field(resp, 'load_duration'))} "
        f"prompt_eval_duration_ms={_ms(_resp_field(resp, 'prompt_eval_duration'))} "
        f"eval_duration_ms={_ms(_resp_field(resp, 'eval_duration'))} "
        f"in_flight={in_flight} model={OLLAMA_MODEL}"
    )

# A stray, unpaired thinking tag (opening or closing). The paired form is
# removed first by _clean_llm_answer; this catches whatever is left. Nothing
# else in angle brackets is touched (see _clean_llm_answer).
_THINK_TAG_RE = re.compile(r'</?think>', re.IGNORECASE)


def _clean_llm_answer(raw_answer: str, *, allow_raw_fallback: bool = True) -> str:
    """Scrub <think> blocks and stray tags, then normalise whitespace.

    Extracted verbatim from generate_answer_with_groq so the streaming and
    non-streaming paths share ONE definition of "the answer text" — the
    reassembled stream is byte-identical to the JSON answer because both end up
    here with the same raw string.

    ``allow_raw_fallback`` is the "cleaning removed everything" rescue. It must
    be OFF for partial text: mid-stream, an unterminated <think> block legitimately
    cleans to "", and falling back to the raw answer there would leak the model's
    chain-of-thought to the user.
    """
    # Strip <think>...</think> only for providers/models that actually emit
    # them (qwen3 on Groq, and all Ollama output since it serves qwen3.5).
    # Llama 4 Scout and other non-thinking Groq models never produce these
    # tags, so the scrub is skipped to avoid eating legitimate <> content.
    #
    # Only the think tags themselves are removed. This used to be a generic
    # ``<[^>]+>`` tag strip, which treated a bare comparison operator as the
    # start of an HTML tag and deleted everything up to the NEXT ">" anywhere
    # later in the answer -- "$D_{50} < 0.5$ mm ... phi > 38" lost the whole
    # span between "<" and ">". Geotechnical answers are full of "<" and ">".
    if _active_model_emits_thinking_tags():
        cleaned_answer = re.sub(r'<think>.*?</think>', '', raw_answer, flags=re.DOTALL | re.IGNORECASE)
        cleaned_answer = _THINK_TAG_RE.sub('', cleaned_answer)
    else:
        cleaned_answer = raw_answer

    # Preserve markdown structure: strip each line individually,
    # then collapse runs of 3+ blank lines down to one blank line.
    lines = cleaned_answer.split('\n')
    final_answer = '\n'.join(line.strip() for line in lines)
    final_answer = re.sub(r'\n{3,}', '\n\n', final_answer).strip()

    # If cleaning removed everything, try to extract content after </think>.
    if allow_raw_fallback and not final_answer and raw_answer:
        print("   [WARNING] Cleaning removed all content, trying to extract after </think>")
        match = re.search(r'</think>\s*(.*)', raw_answer, re.DOTALL | re.IGNORECASE)
        if match:
            final_answer = match.group(1).strip()
            print(f"   [EXTRACTED] Found content after </think>: {len(final_answer)} chars")
        else:
            final_answer = raw_answer.strip()
            print("   [FALLBACK] Using raw answer")

    return final_answer


def _stable_raw_prefix(raw: str) -> str:
    """The longest prefix of ``raw`` whose CLEANED form can no longer change.

    Streaming may only ever emit text that is already final — a token cannot be
    recalled — so this holds back everything the remaining input could still
    rewrite:

      1. An unterminated ``<think>`` block. Its content vanishes once the
         closing tag arrives, so nothing from the opener onward may be emitted.
         This is the tag suppressor: without it the user watches the model's
         chain-of-thought get typed out and then disappear.
      2. A half-received THINK tag (``<``, ``<t``, ``</thin``...). It might
         complete into ``<think>``/``</think>``, which gets stripped. Only a
         trailing ``<`` that is still a prefix of one of those two tags is
         held; a bare comparison ``<`` (``a < b``) is settled text and is
         emitted immediately -- the old rule held at EVERY ``<`` until some
         later ``>`` arrived, which is where the mid-answer stall came from.
      3. Trailing whitespace. Per-line ``strip()`` and the 3+ newline collapse
         both operate on a run that is not finished until a non-space arrives.

    Everything before those points is settled: text only ever appends, so a
    cleaned stable prefix is always a prefix of the next one, and of the final
    answer.
    """
    lower = raw.lower()
    open_idx = lower.rfind("<think>")
    if open_idx != -1 and lower.find("</think>", open_idx) == -1:
        raw = raw[:open_idx]

    lt_idx = raw.rfind("<")
    if lt_idx != -1:
        tail = raw[lt_idx:].lower()
        if len(tail) < len("</think>") and (
            "<think>".startswith(tail) or "</think>".startswith(tail)
        ):
            raw = raw[:lt_idx]

    return raw.rstrip()


async def _ollama_stream_and_clean(
    full_prompt: str, emit: TokenEmitter, *, mode: str = "?"
) -> str:
    """Generate with Ollama in streaming mode, emitting cleaned text as it settles.

    Returns the SAME final string the non-streaming path would return for the
    same raw output, and guarantees the concatenation of everything passed to
    ``emit`` equals that string — so what the user watched, what gets persisted
    and what gets cached are one and the same text.

    ``mode`` is only echoed into the short-answer guard diagnostics.
    """
    global _GENERATIONS_IN_FLIGHT
    client = ollama.AsyncClient(host=OLLAMA_BASE_URL, timeout=OLLAMA_REQUEST_TIMEOUT)
    raw_parts: List[str] = []
    emitted = ""
    timed_out = False
    # Guard diagnostics only: the final chunk carries done_reason/eval counts.
    last_part = None
    thinking_chars = 0
    started = time.monotonic()
    _GENERATIONS_IN_FLIGHT += 1
    in_flight = _GENERATIONS_IN_FLIGHT

    try:
        try:
            stream = await client.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": full_prompt}],
                think=_think_for_answers(),
                options=_ollama_options(),
                stream=True,
            )
            async for part in stream:
                last_part = part
                message = part.get("message") or {}
                thinking_chars += len(message.get("thinking") or "")
                piece = message.get("content") or ""
                if not piece:
                    continue
                raw_parts.append(piece)

                candidate = _clean_llm_answer(
                    _stable_raw_prefix("".join(raw_parts)),
                    allow_raw_fallback=False,
                )
                # Prefix buffer: nothing leaves until the short-answer guard
                # below can no longer need to retry.
                if len(candidate) < SHORT_ANSWER_THRESHOLD:
                    continue
                if not candidate.startswith(emitted):
                    # Should be impossible (see _stable_raw_prefix). Hold rather
                    # than emit text that contradicts what the user already saw.
                    print("   [STREAM] Non-monotonic clean; holding this delta")
                    continue
                delta = candidate[len(emitted):]
                if delta:
                    await emit(delta)
                    emitted = candidate
        except httpx.TimeoutException:
            # Generation exceeded OLLAMA_REQUEST_TIMEOUT part-way through.
            timed_out = True
            print(
                f"   [TIMEOUT] Ollama streaming exceeded {OLLAMA_REQUEST_TIMEOUT}s "
                f"after {len(emitted)} emitted chars"
            )
    finally:
        _GENERATIONS_IN_FLIGHT -= 1
        # Deterministic teardown — on cancellation this is what drops the HTTP
        # connection and makes Ollama abandon the generation. See the
        # non-streaming path for the full rationale.
        try:
            await client.close()
        except Exception as close_err:
            print(f"   [WARNING] Closing Ollama client failed: {close_err}")

    wall_s = time.monotonic() - started
    raw_answer = "".join(raw_parts)

    # Short-answer guard, same threshold and same retry as the non-streaming
    # path. Reachable only while the prefix buffer still holds everything back,
    # so a retry never contradicts text the user has already seen.
    if not timed_out and len(raw_answer.strip()) < SHORT_ANSWER_THRESHOLD:
        print(
            f"   [GUARD] Suspiciously short raw answer "
            f"({len(raw_answer.strip())} chars): {raw_answer!r} — retrying once"
        )
        _log_short_answer_diagnostics(
            last_part, attempt="initial", mode=mode, streaming=True,
            prompt=full_prompt, wall_s=wall_s, in_flight=in_flight,
            thinking_chars=thinking_chars,
        )
        retry_client = ollama.AsyncClient(host=OLLAMA_BASE_URL, timeout=OLLAMA_REQUEST_TIMEOUT)
        retry_started = time.monotonic()
        _GENERATIONS_IN_FLIGHT += 1
        retry_in_flight = _GENERATIONS_IN_FLIGHT
        try:
            resp = await retry_client.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": full_prompt}],
                think=_think_for_answers(),
                options=_ollama_options(),
            )
            raw_answer = resp["message"]["content"] or ""
        finally:
            _GENERATIONS_IN_FLIGHT -= 1
            try:
                await retry_client.close()
            except Exception:
                pass
        if len(raw_answer.strip()) < SHORT_ANSWER_THRESHOLD:
            print(
                f"   [GUARD] Still short after retry "
                f"({len(raw_answer.strip())} chars): {raw_answer!r} — returning fallback"
            )
            _log_short_answer_diagnostics(
                resp, attempt="retry", mode=mode, streaming=False,
                prompt=full_prompt, wall_s=time.monotonic() - retry_started,
                in_flight=retry_in_flight,
            )
            fallback = "I couldn't generate a complete answer — please try again."
            await emit(fallback)
            return fallback

    final_answer = _clean_llm_answer(raw_answer)

    if timed_out and not emitted and not final_answer:
        # Timed out before producing anything usable: identical to the
        # non-streaming timeout behaviour.
        message = "The assistant took too long to respond. Please try again."
        await emit(message)
        return message

    # Flush whatever the stable-prefix rule was still holding back.
    if final_answer.startswith(emitted):
        tail = final_answer[len(emitted):]
        if tail:
            await emit(tail)
            emitted = final_answer
    else:
        # Defensive: never happens with the truncation rules above, but if it
        # ever did, the text on screen is authoritative — do NOT contradict it.
        print(
            "   [STREAM] Final answer diverged from the streamed text; "
            "keeping what the user already received"
        )
        final_answer = emitted

    if timed_out:
        # The user watched a partial answer appear; say so, and persist/cache
        # the SAME text they saw rather than replacing it with a generic notice.
        note = "\n\n_(Response cut short — the assistant timed out.)_"
        await emit(note)
        final_answer += note

    print(f"   [OK] Final answer length: {len(final_answer)} chars (streamed)")
    return final_answer


async def generate_answer_with_groq(
    query: str,
    context: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    mode: str = KB_QUERY,
    system_prompt: Optional[str] = None,
    emit: Optional[TokenEmitter] = None,
) -> str:
    """
    Generate an answer using the configured LLM with an optional RAG context and
    conversation history.

    Args:
        query: The user's question
        context: The relevant context from vector search (formatted string). May
            be empty (GENERAL mode passes "").
        history: Optional conversation history as list of {role, content} dicts
        mode: Router mode selecting the system prompt (prompt_config). Defaults
            to KB_QUERY, which reproduces the pre-router behavior exactly.
        system_prompt: Optional explicit system prompt that overrides the
            mode-keyed lookup (e.g. the THREAD_DOC confidence-fallback prompt).
            When None, the prompt is chosen by ``mode``.
        emit: Optional async callback receiving the answer in pieces as it is
            generated (the SSE chat path supplies one). When None — every other
            caller, including POST /chat — generation is a single blocking call
            and this function behaves exactly as it always has.

    Returns:
        The AI-generated answer as a string

    Raises:
        Exception: If LLM generation fails
    """
    # Initialize LLM
    llm = get_llm()

    # Debug: confirm prior turns actually reach the prompt (wire check).
    if history and len(history) > 0:
        print(f"[PROMPT] Including {len(history)} prior turns (mode={mode})")
    else:
        print(f"[PROMPT] No CONVERSATION HISTORY in prompt (history empty, mode={mode})")

    # Build the complete prompt (system prompt selected by mode, or overridden)
    full_prompt = _build_answer_prompt(query, context, history, mode=mode, system_prompt=system_prompt)

    # Generate response
    try:
        if LLM_PROVIDER == "ollama" and emit is not None:
            # Streaming path: same prompt, same model, same options, same
            # cleaning — delivered incrementally instead of all at once.
            return await _ollama_stream_and_clean(full_prompt, emit, mode=mode)

        if LLM_PROVIDER == "ollama":
            # Bypass llama-index for Ollama: its wrapper does NOT reliably forward
            # the think=false flag (constructor thinking=False -> 70s; the /no_think
            # token -> 120s / 4000 thinking tokens, this model ignores it; a per-call
            # think=False -> timeout). The raw ollama async client honors think=False:
            # ~15s, ~310 tokens, no <think> block. Groq stays on llama-index (else).
            # timeout bounds a hung generation (see OLLAMA_REQUEST_TIMEOUT); on
            # expiry httpx raises TimeoutException, caught below for a clean message.
            client = ollama.AsyncClient(
                host=OLLAMA_BASE_URL, timeout=OLLAMA_REQUEST_TIMEOUT
            )

            async def _ollama_generate():
                """One generate call. Returns (raw_text, response, wall_s,
                in_flight) -- the last three feed the guard diagnostics only."""
                global _GENERATIONS_IN_FLIGHT
                _GENERATIONS_IN_FLIGHT += 1
                in_flight = _GENERATIONS_IN_FLIGHT
                started = time.monotonic()
                try:
                    resp = await client.chat(
                        model=OLLAMA_MODEL,
                        messages=[{"role": "user", "content": full_prompt}],
                        think=_think_for_answers(),
                        options=_ollama_options(),
                    )
                finally:
                    _GENERATIONS_IN_FLIGHT -= 1
                wall_s = time.monotonic() - started
                return (resp["message"]["content"] or ""), resp, wall_s, in_flight

            # `finally: close()` tears the underlying httpx connection down
            # DETERMINISTICALLY instead of leaving it to the garbage collector.
            # It matters most on cancellation: when a caller goes away mid-turn
            # (a browser that closed an SSE stream), CancelledError propagates
            # into the await below, and closing here drops the HTTP connection to
            # Ollama — which is what makes Ollama abandon the generation instead
            # of running it to completion on a GPU nobody is waiting on.
            try:
                raw_answer, resp, wall_s, in_flight = await _ollama_generate()

                # Safety net: a healthy generation is hundreds-to-thousands of chars.
                # A sub-20-char raw answer means the model halted almost immediately
                # (historically caused by num_ctx overflow eating the output budget).
                # With num_ctx now sized for the worst-case prompt this should not
                # happen; if it still does, retry once, then surface a clear message
                # instead of rendering a one-word fragment like "Based".
                if len(raw_answer.strip()) < SHORT_ANSWER_THRESHOLD:
                    print(
                        f"   [GUARD] Suspiciously short raw answer "
                        f"({len(raw_answer.strip())} chars): {raw_answer!r} — retrying once"
                    )
                    _log_short_answer_diagnostics(
                        resp, attempt="initial", mode=mode, streaming=False,
                        prompt=full_prompt, wall_s=wall_s, in_flight=in_flight,
                    )
                    raw_answer, resp, wall_s, in_flight = await _ollama_generate()
                    if len(raw_answer.strip()) < SHORT_ANSWER_THRESHOLD:
                        print(
                            f"   [GUARD] Still short after retry "
                            f"({len(raw_answer.strip())} chars): {raw_answer!r} — returning fallback"
                        )
                        _log_short_answer_diagnostics(
                            resp, attempt="retry", mode=mode, streaming=False,
                            prompt=full_prompt, wall_s=wall_s, in_flight=in_flight,
                        )
                        return "I couldn't generate a complete answer — please try again."
            finally:
                try:
                    await client.close()
                except Exception as close_err:  # never mask the real outcome
                    print(f"   [WARNING] Closing Ollama client failed: {close_err}")
        else:
            response = await llm.acomplete(full_prompt)
            raw_answer = response.text
        
        print(f"   [TEXT] Raw answer length: {len(raw_answer)} chars")
        if len(raw_answer) > 0:
            print(f"   [TEXT] First 300 chars: {raw_answer[:300]}")

        # Shared with the streaming path — one definition of the cleaning, so
        # the two paths cannot drift apart.
        final_answer = _clean_llm_answer(raw_answer)

        print(f"   [CLEAN] Cleaned answer length: {len(final_answer)} chars")
        print(f"   [OK] Final answer length: {len(final_answer)} chars")

        return final_answer
    except httpx.TimeoutException:
        # Ollama generation exceeded OLLAMA_REQUEST_TIMEOUT (worker released, not
        # hung). Return a graceful message inline — matching the short-answer
        # fallback style above — rather than re-raising into a 500.
        print(
            f"   [TIMEOUT] Ollama answer generation exceeded "
            f"{OLLAMA_REQUEST_TIMEOUT}s — returning graceful message"
        )
        return "The assistant took too long to respond. Please try again."
    except Exception as e:
        raise Exception(f"Failed to generate answer with Groq: {str(e)}")


# Deterministic summary-request detection. The rewriter LLM is unreliable for
# these (with long histories it expands "summarize..." into a real topic query
# instead of returning NONE), so we catch them with cheap patterns first.
# Extend this list as we observe phrasings that slip through.
SUMMARY_TRIGGERS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^summari[sz]e",
        r"^sum up",
        r"^recap",
        r"^tl;?dr",
        r"what (have|did) we (discuss|talk about|cover)",
        r"what was (the|our) (discussion|conversation) about",
        r"give me a summary",
        r"can you summari[sz]e",
    )
]


# Unicode ranges for scripts that must never reach the English knowledge base.
# We delete these as a safety net (rules 2/4 of QUERY_REWRITE_PROMPT); Latin
# text, Greek technical symbols (γ, φ, σ, μ), the degree sign and math
# punctuation are all preserved.
_NON_LATIN_SCRIPTS = re.compile(
    "["
    "一-鿿"   # CJK unified ideographs (Chinese / Kanji)
    "぀-ヿ"   # Hiragana + Katakana
    "가-힯"   # Hangul syllables
    "Ѐ-ӿ"   # Cyrillic
    "؀-ۿ"   # Arabic
    "֐-׿"   # Hebrew
    "　-〿"   # CJK symbols & punctuation
    "＀-￯"   # Fullwidth / halfwidth forms
    "]+"
)


def _needs_translation(text: str) -> bool:
    """True when `text` contains characters from a non-Latin script (CJK,
    Cyrillic, Arabic, ...) and therefore needs translating to English. A plain
    Latin-script query (English, possibly with γ/σ/° symbols) returns False, so
    the first-turn rewrite LLM call can be skipped for it."""
    return _NON_LATIN_SCRIPTS.search(text) is not None


def _strip_non_latin(text: str) -> str:
    """Remove any leftover non-Latin-script characters the rewriter may have
    leaked, then collapse whitespace. Greek symbols and ASCII survive. Returns
    "" only if the whole string was non-Latin."""
    return re.sub(r"\s+", " ", _NON_LATIN_SCRIPTS.sub("", text)).strip()


# Merged rewrite prompt: history-aware follow-up resolution + summary detection
# (NONE) + language normalization (translate to English, Latin-script output).
# Used with .format(history_block=..., query=...).
QUERY_REWRITE_PROMPT = """You are a query rewriter for a geotechnical engineering RAG search system. Rewrite the user's LATEST MESSAGE into a single standalone English search query that retrieves well from an English-language knowledge base, using the CONVERSATION HISTORY for context.

STRICT RULES — follow these exactly:
1. Output ONLY the rewritten query. No explanations, no preamble, no quotes, no labels.
2. Output MUST be in English. Use Latin letters, digits, basic punctuation, and standard technical symbols only (e.g. °, μ, γ, φ, σ). Do NOT emit Chinese, Japanese, Korean, Arabic, Cyrillic, or any other non-Latin script.
3. If the LATEST MESSAGE is not in English, translate it to English first, then rewrite.
4. Do NOT keep the original-language text and do NOT add a parenthetical translation — English only.
5. Resolve context from the history: if the LATEST MESSAGE is vague or a follow-up ("how does it differ", "what about clay"), expand it into a self-contained query using the topic from the history.
6. If the LATEST MESSAGE is only an acknowledgement ("ok", "thanks", "continue", "go on"), output the previous USER question (translated to English) instead.
7. If the LATEST MESSAGE asks for a summary or recap of the conversation, output exactly: NONE
8. Keep technical terms (e.g. "Meyerhof", "Nγ", "OCR", "CPT") and numeric values with units (e.g. "35°", "10 kPa", "1.5 m") exactly as written.
9. If the LATEST MESSAGE is already a concise, clear, standalone English query, return it unchanged.
10. Maximum output length: 30 words.

Good rewrites:
- LATEST MESSAGE: "What is the Meyerhof bearing capacity factor Nγ for a friction angle of 35°, and how does it differ from Terzaghi's value?"
  Output: Meyerhof bearing capacity factor Nγ friction angle 35° comparison Terzaghi value
- LATEST MESSAGE: "为什么粘土斜坡分析中要考虑孔隙水压力？" (Chinese)
  Output: pore water pressure consideration in clay slope stability analysis
- HISTORY discusses Terzaghi bearing capacity; LATEST MESSAGE: "how does it differ from Meyerhof?"
  Output: difference between Terzaghi and Meyerhof bearing capacity factors
- LATEST MESSAGE: "summarize what we discussed"
  Output: NONE

Bad rewrites (NEVER do this):
- Mixing languages: "pore water pressure 孔隙水压力 analysis"
- Adding translations: "pore water pressure (孔隙水压力)"
- Adding commentary: "The user is asking about..."
- Wrapping in quotes: "Meyerhof Nγ 35°"

CONVERSATION HISTORY:
{history_block}

LATEST MESSAGE: {query}

Rewritten query:"""


async def rewrite_query_with_history(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Rewrite the user's latest message into a single standalone *English* search
    query for the vector store. Two merged jobs:

      1. Language normalization — translate non-English queries to English and
         emit Latin-script output, so they retrieve against the English KB.
      2. History-aware resolution — expand vague follow-ups ("ok", "how does it
         differ") into self-contained queries using the conversation topic.

    Returns one of:
        - a short standalone English query (str) to embed + search, or
        - "NONE" when the user asks for a summary of the conversation — the
          caller should then skip retrieval and answer from history alone.

    Cost-aware: a plain-English first message (no history) is returned unchanged
    with no LLM call. Falls back to the raw query when there is no API key or the
    LLM call fails, so retrieval still happens.
    """
    # Deterministic summary detection — only meaningful mid-conversation. Skip
    # retrieval (and the rewriter LLM call) when the user asks to summarize.
    normalized = query.strip().lower()
    if history and any(pat.search(normalized) for pat in SUMMARY_TRIGGERS):
        safe_print(f'[QUERY_REWRITE] Original: "{query}" -> SKIPPED (summary detected, no LLM call)')
        return "NONE"

    # Skip the rewrite for a plain-English first turn: nothing to resolve and no
    # translation needed, so the raw query is already optimal (and free).
    if not history and not _needs_translation(query):
        return query

    groq_api_key = os.getenv("GROQ_API_KEY")
    if LLM_PROVIDER == "groq" and not groq_api_key:
        # No key — don't block retrieval, just use the raw query. (Only the Groq
        # provider needs a key; Ollama runs locally without one.)
        return query

    # Dedicated low-temperature, short-output rewriter, separate from the answer
    # LLM (temp 0.3). Groq uses llama-index; Ollama bypasses it and calls the raw
    # ollama async client in the try-block below (the wrapper won't forward
    # think=false), so we only build a llama-index rewriter for the Groq path.
    rewriter = None
    if LLM_PROVIDER == "groq":
        rewriter = Groq(
            model=GROQ_MODEL,
            api_key=groq_api_key,
            temperature=0,
            max_tokens=120,
            request_timeout=30.0,
        )

    # Last 4 turns are enough context to resolve a follow-up. On a first-turn
    # (translation-only) call there is no history, so say so explicitly.
    recent = (history or [])[-4:]
    history_block = (
        "\n".join(
            f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in recent
        )
        if recent
        else "(no prior conversation)"
    )

    rewrite_prompt = QUERY_REWRITE_PROMPT.format(history_block=history_block, query=query)

    try:
        if LLM_PROVIDER == "ollama":
            # Raw ollama client honors think=False; the llama-index wrapper does
            # not (see generate_answer_with_groq for the full diagnosis). Short
            # timeout (OLLAMA_REWRITE_TIMEOUT): this is a tiny rewrite call, and a
            # timeout is caught below to fall back to the raw query.
            client = ollama.AsyncClient(
                host=OLLAMA_BASE_URL, timeout=OLLAMA_REWRITE_TIMEOUT
            )
            resp = await client.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": rewrite_prompt}],
                think=True,
                options=_ollama_options(),
            )
            rewritten = (resp["message"]["content"] or "").strip()
        else:
            response = await rewriter.acomplete(rewrite_prompt)
            rewritten = (response.text or "").strip()

        # Defensive cleanup: drop surrounding quotes/backticks and keep only the
        # first line in case the model adds commentary.
        if rewritten:
            rewritten = rewritten.splitlines()[0].strip().strip('"').strip("'").strip("`").strip()

        if not rewritten:
            return query
        if rewritten.upper() == "NONE":
            return "NONE"

        # Enforce the English contract: strip any leaked non-Latin characters
        # (rules 2/4 of the prompt). If that empties the result (translation
        # failed outright), fall back to the raw query so retrieval still runs.
        rewritten = _strip_non_latin(rewritten) or query

        # Keep it short (~30 words) — guard against the model over-expanding.
        words = rewritten.split()
        if len(words) > 30:
            rewritten = " ".join(words[:30])
        return rewritten
    except Exception as e:
        print(f"[QUERY_REWRITE] Rewrite failed ({e}); using raw query")
        return query

