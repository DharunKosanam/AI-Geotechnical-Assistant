"""Feature A unit tests: the source-formats engine, its prompts, and the
num_ctx isolation contract.

Engine tests monkeypatch the module's _generate (or the raw ollama client) so
no LLM runs; the isolation tests record the options every fake Ollama call
receives and prove a raised format num_ctx can never leak into a chat call.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.core import config
from app.services import llm_service, prompt_config, source_formats
from app.services.intent_router import VALID_MODES

pytestmark = pytest.mark.unit

FIVE_FORMATS = {"study_guide", "briefing", "faq", "timeline", "glossary"}


@pytest.fixture(autouse=True)
def _warm_model(monkeypatch):
    """Engine tests run against a 'warm' Ollama by default so no unit test
    ever probes the live /api/ps. Cold-start tests override per-test."""
    async def warm():
        return True
    monkeypatch.setattr(source_formats, "_model_is_loaded", warm)


# --- format prompts ---------------------------------------------------------
def test_format_prompts_cover_exactly_the_five_formats():
    assert set(prompt_config.FORMAT_PROMPTS) == FIVE_FORMATS


@pytest.mark.parametrize("key", sorted(FIVE_FORMATS))
def test_format_prompt_is_grounded_and_cited(key):
    spec = prompt_config.FORMAT_PROMPTS[key]
    system = spec["system"]
    # Strict grounding: only the provided context, no outside knowledge.
    assert "ONLY the provided context" in system
    assert "Do NOT add outside knowledge" in system
    # Per-source attribution, same [Source: ...] convention as THREAD_DOC.
    assert "[Source:" in system
    assert "NEVER use raw .pdf filenames" in system
    # Multi-document attribution rule carried over from THREAD_DOC.
    assert "MORE THAN ONE attached document" in system
    # Vision-derived content stays marked as model-generated.
    assert "vision" in system.lower()
    # Shared output rules.
    assert "<think>" in system
    assert spec["label"]
    assert spec["instruction"].endswith("above.")


def test_router_mode_prompts_untouched_by_formats():
    # FORMAT_PROMPTS must not leak into the router's mode->prompt map.
    assert set(prompt_config.SYSTEM_PROMPTS) == set(VALID_MODES)
    for key in prompt_config.FORMAT_PROMPTS:
        assert key not in prompt_config.SYSTEM_PROMPTS


# --- token estimation and num_ctx sizing ------------------------------------
def test_estimate_tokens_is_conservative():
    # 3.2 chars/token overestimates vs the measured 3.4-3.5 on corpus text.
    text = "x" * 3200
    assert source_formats.estimate_tokens(text) >= 1000


def test_fit_num_ctx_floors_at_configured_default():
    assert source_formats._fit_num_ctx("short prompt", 2048) == config.OLLAMA_NUM_CTX


def test_fit_num_ctx_scales_with_prompt_and_output_budget():
    prompt = "x" * 320_000  # ~100k estimated tokens
    ctx = source_formats._fit_num_ctx(prompt, 2048)
    assert ctx >= 100_000 + 2048
    assert ctx % 1024 == 0


def test_call_options_are_fresh_per_call_and_config_untouched():
    before = config.OLLAMA_NUM_CTX
    big = source_formats._call_options("x" * 320_000, 2048)
    small = source_formats._call_options("short", 2048)
    assert big["num_ctx"] > config.OLLAMA_NUM_CTX
    assert small["num_ctx"] == config.OLLAMA_NUM_CTX
    assert config.OLLAMA_NUM_CTX == before
    assert llm_service._ollama_options()["num_ctx"] == config.OLLAMA_NUM_CTX


# --- batching ---------------------------------------------------------------
def test_batches_never_span_documents_and_preserve_order():
    doc_blocks = [
        ("a.pdf", [f"a{i}" for i in range(45)]),
        ("b.pdf", [f"b{i}" for i in range(5)]),
    ]
    batches = source_formats._batch_blocks(doc_blocks, 20)
    assert [(fn, s, e, len(blocks)) for fn, s, e, blocks in batches] == [
        ("a.pdf", 1, 20, 20),
        ("a.pdf", 21, 40, 20),
        ("a.pdf", 41, 45, 5),
        ("b.pdf", 1, 5, 5),
    ]
    # Reading order within every batch.
    assert batches[0][3][0] == "a0" and batches[2][3][-1] == "a44"


# --- engine selection -------------------------------------------------------
class _GenSpy:
    """Replaces source_formats._generate; records prompts, returns canned text."""

    def __init__(self, response="GENERATED [Source: doc]"):
        self.calls = []
        self.response = response

    async def __call__(self, prompt, num_predict, emit=None):
        self.calls.append({"prompt": prompt, "num_predict": num_predict,
                           "streamed": emit is not None})
        if emit is not None:
            await emit(self.response)
        return self.response


@pytest.mark.asyncio
async def test_small_document_takes_single_call(monkeypatch):
    spy = _GenSpy()
    monkeypatch.setattr(source_formats, "_generate", spy)
    emitted = []

    async def emit(t):
        emitted.append(t)

    text, meta = await source_formats.generate_format_document(
        "briefing", [("doc.pdf", ["[Source: doc]\nchunk one", "[Source: doc]\nchunk two"])],
        emit=emit,
    )
    assert meta["engine"] == "single_call"
    assert meta["chunks"] == 2
    assert len(spy.calls) == 1 and spy.calls[0]["streamed"]
    # The single prompt carries every chunk and the format instruction.
    assert "chunk one" in spy.calls[0]["prompt"]
    assert "chunk two" in spy.calls[0]["prompt"]
    assert "briefing document" in spy.calls[0]["prompt"].lower()
    assert "".join(emitted) == text == spy.response


@pytest.mark.asyncio
async def test_large_document_takes_map_reduce_with_progress(monkeypatch):
    spy = _GenSpy(response="note [Source: doc]")
    monkeypatch.setattr(source_formats, "_generate", spy)
    # Threshold chosen between the full-context prompt (~7k est tokens with the
    # 4k-char blocks below) and the notes-only reduce prompt (~1k): map-reduce
    # is forced, but the reduce fits in one call with no condense level.
    monkeypatch.setattr(config, "SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS", 2000)
    monkeypatch.setattr(config, "SOURCE_FORMATS_MAP_BATCH_CHUNKS", 2)

    progress_events = []

    async def progress(stage, current, total):
        progress_events.append((stage, current, total))

    blocks = [(f"[Source: doc]\nchunk {i} " + "x" * 4000) for i in range(5)]
    text, meta = await source_formats.generate_format_document(
        "faq", [("doc.pdf", blocks)], progress=progress,
    )
    assert meta["engine"] == "map_reduce"
    assert meta["mapCalls"] == 3  # 2+2+1 chunks
    # 3 map calls + 1 final reduce; only the final streams.
    assert len(spy.calls) == 4
    assert [c["streamed"] for c in spy.calls] == [False, False, False, False]
    assert [p for p in progress_events if p[0] == "map"] == [
        ("map", 1, 3), ("map", 2, 3), ("map", 3, 3)
    ]
    assert progress_events[-1] == ("reduce", 1, 1)
    # The reduce prompt is built from the notes, labeled per source.
    assert "[Notes on 'doc.pdf' sections 1-2]" in spy.calls[-1]["prompt"]
    assert text == spy.response


@pytest.mark.asyncio
async def test_map_batches_never_mix_documents(monkeypatch):
    spy = _GenSpy(response="note")
    monkeypatch.setattr(source_formats, "_generate", spy)
    # As above: force map-reduce while the notes-only reduce still fits.
    monkeypatch.setattr(config, "SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS", 2000)
    monkeypatch.setattr(config, "SOURCE_FORMATS_MAP_BATCH_CHUNKS", 10)

    await source_formats.generate_format_document(
        "glossary",
        [("a.pdf", ["[Source: A]\naaa " + "x" * 4000]),
         ("b.pdf", ["[Source: B]\nbbb " + "y" * 4000])],
    )
    map_prompts = [c["prompt"] for c in spy.calls[:-1]]
    assert len(map_prompts) == 2
    assert "aaa" in map_prompts[0] and "bbb" not in map_prompts[0]
    assert "bbb" in map_prompts[1] and "aaa" not in map_prompts[1]


@pytest.mark.asyncio
async def test_single_call_reports_read_estimate_then_write(monkeypatch):
    """The prefill wait is reported: a 'read' stage with a rate-derived
    estimate before the call, flipping to 'write' on the first token."""
    spy = _GenSpy()
    monkeypatch.setattr(source_formats, "_generate", spy)
    events, emitted = [], []

    async def progress(stage, current, total):
        events.append((stage, current, total))

    async def emit(t):
        emitted.append(t)

    text, meta = await source_formats.generate_format_document(
        "briefing", [("doc.pdf", ["[Source: doc]\nchunk one"])],
        emit=emit, progress=progress,
    )
    stages = [e[0] for e in events]
    assert stages[0] == "read"
    assert "write" in stages
    assert stages.index("read") < stages.index("write")
    # The read estimate covers at least the measured context-swap allowance.
    assert events[0][2] >= source_formats._CTX_SWAP_ALLOWANCE_S
    assert "".join(emitted) == text  # the wrapper still delivers every token


@pytest.mark.asyncio
async def test_cold_model_adds_load_stage_and_allowance(monkeypatch):
    """A model that is not resident in Ollama gets a distinct 'load' stage
    and the cold allowance folded into the read estimate -- otherwise the
    estimate undershoots by ~2x exactly on first use after a restart."""
    spy = _GenSpy()
    monkeypatch.setattr(source_formats, "_generate", spy)

    async def cold():
        return False
    monkeypatch.setattr(source_formats, "_model_is_loaded", cold)

    events = []

    async def progress(stage, current, total):
        events.append((stage, current, total))

    await source_formats.generate_format_document(
        "briefing", [("doc.pdf", ["[Source: doc]\nchunk"])], progress=progress,
    )
    stages = [e[0] for e in events]
    assert stages[0] == "load"
    assert events[0][2] == source_formats._COLD_LOAD_ALLOWANCE_S
    read = next(e for e in events if e[0] == "read")
    assert read[2] >= source_formats._COLD_LOAD_ALLOWANCE_S


@pytest.mark.asyncio
async def test_warm_model_has_no_load_stage(monkeypatch):
    spy = _GenSpy()
    monkeypatch.setattr(source_formats, "_generate", spy)
    events = []

    async def progress(stage, current, total):
        events.append((stage, current, total))

    await source_formats.generate_format_document(
        "briefing", [("doc.pdf", ["[Source: doc]\nchunk"])], progress=progress,
    )
    stages = [e[0] for e in events]
    assert "load" not in stages
    read = next(e for e in events if e[0] == "read")
    assert read[2] < source_formats._COLD_LOAD_ALLOWANCE_S


@pytest.mark.asyncio
async def test_ps_probe_failure_treated_as_warm(monkeypatch):
    """Ollama unreachable for the probe must not block or mislabel -- the
    estimate just keeps its optimistic default."""
    spy = _GenSpy()
    monkeypatch.setattr(source_formats, "_generate", spy)

    async def unknown():
        return None
    monkeypatch.setattr(source_formats, "_model_is_loaded", unknown)

    events = []

    async def progress(stage, current, total):
        events.append((stage, current, total))

    await source_formats.generate_format_document(
        "briefing", [("doc.pdf", ["[Source: doc]\nchunk"])], progress=progress,
    )
    assert "load" not in [e[0] for e in events]


@pytest.mark.asyncio
async def test_unshrinkable_reduce_runs_oversized_instead_of_looping(monkeypatch):
    """Regression: a threshold the reduce prompt can never fit under must run
    the reduce oversized (num_ctx is sized to the real prompt), not condense
    forever."""
    spy = _GenSpy(response="note")
    monkeypatch.setattr(source_formats, "_generate", spy)
    monkeypatch.setattr(config, "SOURCE_FORMATS_SINGLE_CALL_MAX_TOKENS", 50)
    monkeypatch.setattr(config, "SOURCE_FORMATS_MAP_BATCH_CHUNKS", 2)

    text, meta = await source_formats.generate_format_document(
        "timeline", [("doc.pdf", ["[Source: doc]\nchunk " + "x" * 400])],
    )
    assert meta["engine"] == "map_reduce"
    assert text == spy.response  # it completed


# --- think=False + per-request options on the raw calls ---------------------
class _FakeOllamaClient:
    """Stands in for ollama.AsyncClient in BOTH source_formats and llm_service.
    Records every chat() kwargs so tests can assert think/options per call."""

    recorded = []

    def __init__(self, *a, **k):
        pass

    async def chat(self, **kwargs):
        _FakeOllamaClient.recorded.append(kwargs)
        if kwargs.get("stream"):
            async def _stream():
                yield {"message": {"content": "streamed answer body, long enough to clear the guard."}}
            return _stream()
        return {"message": {"content": "non-streaming answer body, long enough to clear the guard."}}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_generate_passes_think_true_and_sized_ctx(monkeypatch):
    _FakeOllamaClient.recorded = []
    monkeypatch.setattr(source_formats.ollama, "AsyncClient", _FakeOllamaClient)
    big_prompt = "x" * 320_000
    await source_formats._generate(big_prompt, 2048)
    call = _FakeOllamaClient.recorded[0]
    assert call["think"] is True
    assert call["options"]["num_ctx"] > config.OLLAMA_NUM_CTX
    assert call["options"]["num_predict"] == 2048


@pytest.mark.asyncio
async def test_format_ctx_raise_never_leaks_into_chat_call(monkeypatch):
    """The isolation contract: a format call at raised num_ctx followed by a
    normal chat generation must show the chat call using the configured
    default. This is the setting behind the old context-overflow bug."""
    _FakeOllamaClient.recorded = []
    monkeypatch.setattr(source_formats.ollama, "AsyncClient", _FakeOllamaClient)
    monkeypatch.setattr(llm_service.ollama, "AsyncClient", _FakeOllamaClient)
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "ollama")

    # 1) Format call sized far above the default window.
    await source_formats._generate("x" * 320_000, 2048)
    # 2) Normal chat generation immediately after.
    await llm_service.generate_answer_with_groq(
        query="What is dilatancy?", context="[Source: X]\nctx", history=None
    )

    fmt_call, chat_call = _FakeOllamaClient.recorded[0], _FakeOllamaClient.recorded[-1]
    assert fmt_call["options"]["num_ctx"] > config.OLLAMA_NUM_CTX
    assert chat_call["options"]["num_ctx"] == config.OLLAMA_NUM_CTX
    # Answer path carries its own (larger) output budget; the format engine
    # and rewriter keep the default. See config.ANSWER_NUM_PREDICT.
    assert chat_call["options"] == llm_service._ollama_options(
        num_predict=config.ANSWER_NUM_PREDICT
    )
    # The answer call's think flag follows OLLAMA_THINK_ANSWERS (default off
    # since 2026-08-26); see test_answer_think_flag.py for both settings.
    assert chat_call["think"] is config.OLLAMA_THINK_ANSWERS
    # And the config constant itself never moved.
    assert config.OLLAMA_NUM_CTX == int(
        __import__("os").getenv("OLLAMA_NUM_CTX", "12288")
    )


# --- streaming emit contract ------------------------------------------------
@pytest.mark.asyncio
async def test_streamed_emits_concatenate_to_returned_text(monkeypatch):
    _FakeOllamaClient.recorded = []
    monkeypatch.setattr(source_formats.ollama, "AsyncClient", _FakeOllamaClient)
    pieces = []

    async def emit(t):
        pieces.append(t)

    out = await source_formats._generate("prompt", 2048, emit=emit)
    assert "".join(pieces) == out
    assert len(out) > 0
