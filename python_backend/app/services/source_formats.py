"""Source-grounded output format engine (SOURCE_FORMATS_ENABLED, default off).

Generates a structured document (study guide, briefing, FAQ, timeline,
glossary) from EVERY chunk of a thread's ready documents. Two engine paths,
chosen automatically from the measured prompt size -- never by the user:

  single_call -- the whole document set in one generation. num_ctx is computed
      PER REQUEST from the actual prompt size (Ollama options are per-request;
      nothing here touches config or llm_service state, so the chat and KB
      paths keep their configured default -- asserted by the isolation test).
      Measured viable to ~90k prompt tokens on the deployment GPU before the
      KV cache spills to CPU.

  map_reduce -- above the single-call bound: reading-order batches are
      summarized into notes (map), and the notes are synthesized into the
      final document (reduce), hierarchically when the notes themselves
      overflow one call. Batches never span documents, so every note is
      single-source and per-source attribution survives the bottleneck.

Both paths send NO conversation history (the 6k-token history reserve is not
needed for a button-triggered generation and would cost coverage), pass
think=False on every raw call (without it gemma4 burns the entire output
budget in its thinking channel and returns empty text), and stream the final
document through the same cleaning helpers as the chat path so the two can
never drift.

Progress contract: the single-call path is silent at the LLM level until the
first token -- a num_ctx swap (~6s measured) plus a full-document prefill
(~65s at 100 chunks) with nothing observable from outside. The engine
therefore reports stages itself: "read" with an estimate derived from the
measured rates and the actual prompt size, ticking elapsed seconds until the
first token, then "write". Users read silence as a hang; a moving counter
with an honest estimate is the fix (the wait itself is physics).
"""

import asyncio
import math
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
import ollama

from app.core import config
from app.services.llm_service import (
    SHORT_ANSWER_THRESHOLD,
    TokenEmitter,
    _build_answer_prompt,
    _clean_llm_answer,
    _stable_raw_prefix,
)
from app.services.prompt_config import FORMAT_PROMPTS

# Progress callback: (stage, current, total). Stages: "map", "reduce",
# "generate". The SSE route forwards these as progress events.
ProgressEmitter = Callable[[str, int, int], Awaitable[None]]

# Conservative chars-per-token divisor. Measured 3.4-3.5 chars/token on real
# corpus text (gemma tokenizer, geotech content); 3.2 overestimates the token
# count by ~8% so a fat document can never overflow the window it was sized to.
_CHARS_PER_TOKEN = 3.2

# Headroom added to every computed num_ctx: template tokens, BOS/EOS, and the
# estimator's granularity.
_CTX_MARGIN_TOKENS = 256

# User-facing time estimates for the single-call "read" stage, from the
# measured deployment rates (prefill ~715 tok/s at 10k falling to ~590 at
# 36k; generation ~24 tok/s; num_ctx swap ~6s). Deliberately conservative so
# the counter beats the estimate more often than it overruns it.
_PREFILL_TOK_PER_S = 550
_GEN_TOK_PER_S = 22
_CTX_SWAP_ALLOWANCE_S = 10
# Cadence of elapsed-seconds progress ticks while waiting on the prefill.
_READ_TICK_SECONDS = 5.0

# Cold-start allowance: when Ollama has NO copy of the model resident (after
# a host reboot or Ollama restart -- keep_alive pinning covers the idle
# case), the first call pays a full weights load from disk: 113s measured
# cold on 2026-08-04 (6.9GB blob, rotational-class virtio storage). Without
# this the read estimate undershoots by ~2x exactly when an unfamiliar user
# is most likely to conclude the feature is broken.
_COLD_LOAD_ALLOWANCE_S = 120


async def _model_is_loaded() -> Optional[bool]:
    """Whether the configured model is currently resident in Ollama.

    Read from /api/ps with a short timeout; returns None (treated as warm)
    when Ollama cannot be asked -- the estimate then simply keeps its
    optimistic default rather than blocking the generation on a probe."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{config.OLLAMA_BASE_URL}/api/ps")
            r.raise_for_status()
            models = r.json().get("models") or []
            return any(m.get("name") == config.OLLAMA_MODEL for m in models)
    except Exception:
        return None

# Engine-internal map/reduce scaffolding. The five user-facing format prompts
# live in prompt_config.FORMAT_PROMPTS; these two exist only to move content
# through the map-reduce bottleneck faithfully.
_MAP_NOTES_SYSTEM = """You are an expert AI assistant creating faithful working notes from a section of a document the user uploaded.

STRICT GROUNDING:
- Use ONLY the provided context. Do NOT add outside knowledge.
- Keep every concrete fact, figure, parameter, named method, date, and conclusion. Dense notes beat prose.
- Keep the [Source: ...] attribution for every fact, using the titles in the [Source: ...] tags, never raw .pdf filenames.
- Some context may be labeled as AI vision transcription or description; carry that label forward on those facts.

CRITICAL: Do NOT use <think> tags or any XML tags. Output the notes directly."""

_MAP_NOTES_INSTRUCTION = (
    "Write dense, faithful notes on the section content above, keeping all "
    "concrete facts, figures, parameters, and conclusions with their [Source: ...] attribution."
)

_REDUCE_PREAMBLE = (
    "The context above consists of faithful working notes covering consecutive "
    "sections of the source material, in reading order, together spanning ALL "
    "of it. Synthesize the requested document from these notes as if from the "
    "source itself, keeping the [Source: ...] attribution intact. "
)


def estimate_tokens(text: str) -> int:
    """Conservative (over-)estimate of the gemma token count of ``text``."""
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def _fit_num_ctx(prompt: str, num_predict: int) -> int:
    """num_ctx for one call: the estimated prompt plus the full output budget
    plus margin, rounded up to 1k, floored at the configured default so small
    documents never trigger a model reload away from the chat path's size."""
    needed = estimate_tokens(prompt) + num_predict + _CTX_MARGIN_TOKENS
    return max(config.OLLAMA_NUM_CTX, math.ceil(needed / 1024) * 1024)


def _call_options(prompt: str, num_predict: int) -> dict:
    """Per-request options. Built fresh for every call from config constants --
    never cached, never shared, never written back anywhere."""
    return {
        "num_ctx": _fit_num_ctx(prompt, num_predict),
        "num_predict": num_predict,
        "temperature": config.OLLAMA_TEMPERATURE,
    }


async def _generate(
    prompt: str,
    num_predict: int,
    emit: Optional[TokenEmitter] = None,
) -> str:
    """One Ollama generation with per-request options and the format timeout.

    Mirrors llm_service's raw-call behavior: think=False, deterministic client
    close (a cancelled SSE consumer drops the HTTP connection so Ollama
    abandons the generation), the shared cleaning helpers, one retry on a
    suspiciously short answer, and the stable-prefix rule when streaming so
    emitted text is always a prefix of the final cleaned answer.
    """
    options = _call_options(prompt, num_predict)
    client = ollama.AsyncClient(
        host=config.OLLAMA_BASE_URL, timeout=config.SOURCE_FORMATS_TIMEOUT
    )
    raw_parts: List[str] = []
    emitted = ""
    timed_out = False
    # Wall-clock-in-message + flush: immune to stdout block buffering. The
    # gap between these two lines is Ollama's model (re)load PLUS prefill --
    # measured 113s for a cold-page-cache load of the 6.9GB blob alone.
    print(f"[FORMATS-TIMING] {time.strftime('%H:%M:%S')} ollama call dispatched "
          f"(num_ctx={options['num_ctx']})", flush=True)
    _first_chunk_logged = False
    try:
        try:
            if emit is None:
                resp = await client.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    think=True,
                    options=options,
                )
                raw_parts.append(resp["message"]["content"] or "")
            else:
                stream = await client.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    think=True,
                    options=options,
                    stream=True,
                )
                async for part in stream:
                    if not _first_chunk_logged:
                        _first_chunk_logged = True
                        print(f"[FORMATS-TIMING] {time.strftime('%H:%M:%S')} "
                              f"first ollama chunk received", flush=True)
                    piece = ((part.get("message") or {}).get("content")) or ""
                    if not piece:
                        continue
                    raw_parts.append(piece)
                    candidate = _clean_llm_answer(
                        _stable_raw_prefix("".join(raw_parts)),
                        allow_raw_fallback=False,
                    )
                    if len(candidate) < SHORT_ANSWER_THRESHOLD:
                        continue
                    if not candidate.startswith(emitted):
                        continue
                    delta = candidate[len(emitted):]
                    if delta:
                        await emit(delta)
                        emitted = candidate
        except httpx.TimeoutException:
            timed_out = True
            print(
                f"[FORMATS] Ollama call exceeded {config.SOURCE_FORMATS_TIMEOUT}s "
                f"(num_ctx={options['num_ctx']}, {len(emitted)} chars emitted)"
            )
    finally:
        try:
            await client.close()
        except Exception as close_err:
            print(f"[FORMATS] Closing Ollama client failed: {close_err}")

    raw_answer = "".join(raw_parts)

    if not timed_out and len(raw_answer.strip()) < SHORT_ANSWER_THRESHOLD:
        print(
            f"[FORMATS] Suspiciously short raw answer "
            f"({len(raw_answer.strip())} chars) -- retrying once"
        )
        retry = ollama.AsyncClient(
            host=config.OLLAMA_BASE_URL, timeout=config.SOURCE_FORMATS_TIMEOUT
        )
        try:
            resp = await retry.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                think=True,
                options=options,
            )
            raw_answer = resp["message"]["content"] or ""
        finally:
            try:
                await retry.close()
            except Exception as close_err:
                print(f"[FORMATS] Closing Ollama retry client failed: {close_err}")

    final = _clean_llm_answer(raw_answer)

    if emit is not None:
        if final.startswith(emitted):
            tail = final[len(emitted):]
            if tail:
                await emit(tail)
        else:
            # Never contradict text the user already watched appear.
            final = emitted
        if timed_out:
            note = "\n\n_(Generation cut short -- the model timed out.)_"
            await emit(note)
            final += note
    elif timed_out and not final:
        raise TimeoutError("format generation call timed out with no output")

    return final


def _batch_blocks(
    doc_blocks: List[Tuple[str, List[str]]],
    batch_chunks: int,
) -> List[Tuple[str, int, int, List[str]]]:
    """Reading-order batches that never span documents:
    [(filename, start_index, end_index, blocks)]."""
    batches = []
    for filename, blocks in doc_blocks:
        for i in range(0, len(blocks), batch_chunks):
            part = blocks[i:i + batch_chunks]
            batches.append((filename, i + 1, i + len(part), part))
    return batches


async def generate_format_document(
    format_key: str,
    doc_blocks: List[Tuple[str, List[str]]],
    context_header: str = "",
    emit: Optional[TokenEmitter] = None,
    progress: Optional[ProgressEmitter] = None,
) -> Tuple[str, Dict]:
    """Generate one format document over the full content of ``doc_blocks``.

    doc_blocks: [(filename, [context_block, ...])] in reading order -- context
    blocks are pre-rendered by the caller with the SAME formatter as THREAD_DOC
    answers, so [Source: ...] titles and vision labels are identical there.
    context_header: the [ATTACHED DOCUMENTS] / [CONTEXT DRAWN FROM] preamble
    for multi-document threads ("" for single-document, matching chat).

    Returns (document_text, meta); meta records the chosen engine path and its
    shape for the response metadata and the persisted message.
    """
    spec = FORMAT_PROMPTS[format_key]

    async def _report(stage: str, current: int, total: int) -> None:
        if progress is not None:
            await progress(stage, current, total)

    all_blocks: List[str] = [b for _, blocks in doc_blocks for b in blocks]
    full_context = "\n\n".join(all_blocks)
    if context_header:
        full_context = f"{context_header}\n\n{full_context}"
    single_prompt = _build_answer_prompt(
        spec["instruction"], full_context, None, system_prompt=spec["system"]
    )
    est = estimate_tokens(single_prompt)

    if est <= config.SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS:
        print(
            f"[FORMATS] engine=single_call format={format_key} "
            f"chunks={len(all_blocks)} est_tokens={est} "
            f"num_ctx={_fit_num_ctx(single_prompt, config.OLLAMA_NUM_PREDICT)}"
        )
        # The call is silent until the first token (context swap + whole-
        # document prefill), so report the wait ourselves: "read" with an
        # estimate from the measured rates and THIS prompt's size, ticking
        # elapsed seconds, then "write" the moment the first token lands.
        # A cold model (nothing resident in Ollama) first gets a distinct
        # "load" stage and its allowance folded into the read total.
        est_read_s = _CTX_SWAP_ALLOWANCE_S + est // _PREFILL_TOK_PER_S
        est_write_s = config.OLLAMA_NUM_PREDICT // _GEN_TOK_PER_S
        if await _model_is_loaded() is False:
            await _report("load", 0, _COLD_LOAD_ALLOWANCE_S)
            est_read_s += _COLD_LOAD_ALLOWANCE_S
        await _report("read", 0, est_read_s)
        started = time.monotonic()
        first_token = asyncio.Event()

        async def _ticker() -> None:
            while not first_token.is_set():
                try:
                    await asyncio.wait_for(first_token.wait(), _READ_TICK_SECONDS)
                except asyncio.TimeoutError:
                    await _report("read", int(time.monotonic() - started), est_read_s)

        wrapped = emit
        if emit is not None:
            async def wrapped(text: str) -> None:  # noqa: F811 -- deliberate rebind
                if not first_token.is_set():
                    first_token.set()
                    await _report("write", 0, est_write_s)
                await emit(text)

        ticker = asyncio.create_task(_ticker()) if progress is not None else None
        try:
            text = await _generate(single_prompt, config.OLLAMA_NUM_PREDICT, emit=wrapped)
        finally:
            first_token.set()
            if ticker is not None:
                ticker.cancel()
        return text, {
            "engine": "single_call",
            "chunks": len(all_blocks),
            "estPromptTokens": est,
        }

    # --- map ---------------------------------------------------------------
    batches = _batch_blocks(doc_blocks, config.SOURCE_FORMATS_MAP_BATCH_CHUNKS)
    print(
        f"[FORMATS] engine=map_reduce format={format_key} "
        f"chunks={len(all_blocks)} est_tokens={est} map_batches={len(batches)}"
    )
    # The first map batch pays any cold model load too -- name that wait.
    if await _model_is_loaded() is False:
        await _report("load", 0, _COLD_LOAD_ALLOWANCE_S)
    notes: List[Tuple[str, str]] = []  # (filename, note text)
    for i, (filename, start, end, blocks) in enumerate(batches):
        await _report("map", i + 1, len(batches))
        batch_context = "\n\n".join(blocks)
        map_prompt = _build_answer_prompt(
            _MAP_NOTES_INSTRUCTION, batch_context, None,
            system_prompt=_MAP_NOTES_SYSTEM,
        )
        note = await _generate(map_prompt, config.SOURCE_FORMATS_MAP_NUM_PREDICT)
        notes.append((filename, f"[Notes on '{filename}' sections {start}-{end}]\n{note}"))
    map_calls = len(batches)

    # --- reduce (hierarchical when the notes overflow one call) ------------
    reduce_levels = 0
    while True:
        reduce_levels += 1
        notes_context = "\n\n".join(n for _, n in notes)
        if context_header:
            notes_context = f"{context_header}\n\n{notes_context}"
        final_prompt = _build_answer_prompt(
            spec["instruction"], notes_context, None,
            system_prompt=spec["system"] + "\n\n" + _REDUCE_PREAMBLE,
        )
        if estimate_tokens(final_prompt) <= config.SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS:
            break
        if len(notes) <= 1 or reduce_levels > 3:
            # Condensation can shrink no further (or is pathologically deep);
            # run the reduce oversized rather than loop -- num_ctx is sized to
            # the actual prompt, so the bound above is the GPU-residency
            # guideline, not a hard limit.
            print(
                f"[FORMATS] reduce prompt still ~{estimate_tokens(final_prompt)} "
                f"tokens after {reduce_levels - 1} condense level(s) -- running oversized"
            )
            break
        # Condense consecutive same-source note groups one level. Group size
        # ~15 compresses each round by an order of magnitude, so this
        # terminates in one extra level for any realistic corpus.
        await _report("reduce", 0, len(notes))

        async def _condense(group: List[Tuple[str, str]]) -> Tuple[str, str]:
            gctx = "\n\n".join(n for _, n in group)
            gprompt = _build_answer_prompt(
                _MAP_NOTES_INSTRUCTION, gctx, None,
                system_prompt=_MAP_NOTES_SYSTEM,
            )
            gnote = await _generate(gprompt, config.SOURCE_FORMATS_MAP_NUM_PREDICT)
            fn = group[0][0]
            return fn, f"[Condensed notes on '{fn}']\n{gnote}"

        condensed: List[Tuple[str, str]] = []
        group: List[Tuple[str, str]] = []
        for item in notes:
            # Never merge notes across documents: flush at a source boundary.
            if group and group[0][0] != item[0]:
                condensed.append(await _condense(group))
                group = []
            group.append(item)
            if len(group) >= 15:
                condensed.append(await _condense(group))
                group = []
        if group:
            condensed.append(await _condense(group))
        notes = condensed

    await _report("reduce", 1, 1)
    text = await _generate(final_prompt, config.OLLAMA_NUM_PREDICT, emit=emit)
    return text, {
        "engine": "map_reduce",
        "chunks": len(all_blocks),
        "estPromptTokens": est,
        "mapCalls": map_calls,
        "reduceLevels": reduce_levels,
    }
