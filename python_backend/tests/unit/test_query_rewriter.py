"""
Unit tests for the query rewriter (Problem 1) -- llm_service.

Covers the two pure helpers (_needs_translation, _strip_non_latin) and the full
rewrite_query_with_history flow with the Groq LLM mocked, so the tests are
deterministic and make no network calls. The mock also lets us assert the
cost-aware paths that must NOT call the LLM at all.

Run via pytest (async tests use pytest.ini's asyncio_mode=auto):
    pytest tests/unit/test_query_rewriter.py -v
"""
import os
import sys
from types import SimpleNamespace

# python_backend/ on path so `import app...` works under pytest and standalone.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services import llm_service
from app.services.llm_service import (
    _needs_translation,
    _strip_non_latin,
    rewrite_query_with_history,
)


def _install_fake_llm(monkeypatch, return_text="", raise_exc=None):
    """Make the rewriter's LLM call deterministic under EITHER provider.

    rewrite_query_with_history uses the raw ``ollama.AsyncClient`` when
    LLM_PROVIDER == "ollama" and llama-index ``Groq`` otherwise. The live deploy
    runs Ollama, so mocking only Groq left these tests hitting real Ollama
    (non-deterministic). We patch BOTH paths to return ``return_text`` (or raise)
    and share one instantiation counter, so the assertions hold regardless of the
    configured provider and no network call is ever made.
    """
    state = {"instantiated": 0, "prompt": None}

    class _FakeGroq:
        def __init__(self, *args, **kwargs):
            state["instantiated"] += 1

        async def acomplete(self, prompt):
            state["prompt"] = prompt
            if raise_exc is not None:
                raise raise_exc
            return SimpleNamespace(text=return_text)

    class _FakeOllamaClient:
        def __init__(self, *args, **kwargs):
            state["instantiated"] += 1

        async def chat(self, *args, **kwargs):
            state["prompt"] = kwargs.get("messages")
            if raise_exc is not None:
                raise raise_exc
            return {"message": {"content": return_text}}

    monkeypatch.setattr(llm_service, "Groq", _FakeGroq)
    monkeypatch.setattr(llm_service.ollama, "AsyncClient", _FakeOllamaClient)
    return state


# Back-compat alias: existing call sites used _install_fake_groq.
_install_fake_groq = _install_fake_llm


# ---------------------------------------------------------------------------
# _needs_translation -- True only for non-Latin scripts
# ---------------------------------------------------------------------------
def test_needs_translation_false_for_plain_english():
    assert _needs_translation("What is soil bearing capacity?") is False


def test_needs_translation_false_for_greek_and_degree_symbols():
    # Greek technical symbols and the degree sign are allowed Latin-adjacent text.
    assert _needs_translation("Meyerhof Nγ at friction angle 35° with σ and φ") is False


def test_needs_translation_true_for_non_latin_scripts():
    assert _needs_translation("为什么粘土斜坡要考虑孔隙水压力？") is True   # Chinese
    assert _needs_translation("давление поровой воды") is True            # Cyrillic
    assert _needs_translation("ضغط الماء المسامي") is True                # Arabic


# ---------------------------------------------------------------------------
# _strip_non_latin -- delete non-Latin chars, keep Greek/ASCII, collapse ws
# ---------------------------------------------------------------------------
def test_strip_non_latin_removes_cjk_and_collapses_whitespace():
    assert _strip_non_latin("pore water 孔隙水压力 analysis") == "pore water analysis"


def test_strip_non_latin_keeps_greek_and_ascii():
    assert _strip_non_latin("Meyerhof Nγ 35°") == "Meyerhof Nγ 35°"


def test_strip_non_latin_empty_when_all_non_latin():
    assert _strip_non_latin("孔隙水压力") == ""


# ---------------------------------------------------------------------------
# rewrite_query_with_history -- cost-aware skip paths (NO LLM call)
# ---------------------------------------------------------------------------
async def test_plain_english_first_turn_returned_unchanged_without_llm(monkeypatch):
    state = _install_fake_groq(monkeypatch, return_text="SHOULD NOT BE USED")
    query = "What is the role of Atterberg limits in soil classification?"
    out = await rewrite_query_with_history(query, history=None)
    assert out == query
    assert state["instantiated"] == 0   # cost-aware: no LLM call for plain English


async def test_summary_request_with_history_returns_none_without_llm(monkeypatch):
    state = _install_fake_groq(monkeypatch, return_text="SHOULD NOT BE USED")
    history = [
        {"role": "user", "content": "Tell me about bearing capacity."},
        {"role": "assistant", "content": "Bearing capacity is ..."},
    ]
    out = await rewrite_query_with_history("summarize what we discussed", history)
    assert out == "NONE"
    assert state["instantiated"] == 0   # deterministic summary detection, no LLM call


# ---------------------------------------------------------------------------
# rewrite_query_with_history -- LLM paths (mocked)
# ---------------------------------------------------------------------------
async def test_non_english_first_turn_is_translated(monkeypatch):
    english = "pore water pressure consideration in clay slope stability analysis"
    state = _install_fake_groq(monkeypatch, return_text=english)
    out = await rewrite_query_with_history("为什么粘土斜坡分析中要考虑孔隙水压力？", history=None)
    assert out == english
    assert state["instantiated"] == 1   # translation needed -> LLM was called


async def test_leaked_non_latin_chars_are_stripped_from_llm_output(monkeypatch):
    _install_fake_groq(monkeypatch, return_text="pore water 压力 analysis")
    out = await rewrite_query_with_history("孔隙水压力分析", history=None)
    assert out == "pore water analysis"


async def test_quotes_are_stripped_from_llm_output(monkeypatch):
    _install_fake_groq(monkeypatch, return_text='"Meyerhof Nγ 35°"')
    history = [{"role": "user", "content": "Tell me about bearing capacity factors."}]
    out = await rewrite_query_with_history("and Nγ?", history)
    assert out == "Meyerhof Nγ 35°"


async def test_llm_none_passthrough(monkeypatch):
    # A vague ack with history reaches the LLM (not a summary trigger); if the
    # model decides it's a summary it returns NONE, which must pass through.
    _install_fake_groq(monkeypatch, return_text="NONE")
    history = [{"role": "user", "content": "Explain consolidation settlement."}]
    out = await rewrite_query_with_history("ok", history)
    assert out == "NONE"


async def test_history_followup_is_expanded(monkeypatch):
    expanded = "difference between Terzaghi and Meyerhof bearing capacity factors"
    _install_fake_groq(monkeypatch, return_text=expanded)
    history = [
        {"role": "user", "content": "What is Terzaghi bearing capacity?"},
        {"role": "assistant", "content": "It is ..."},
    ]
    out = await rewrite_query_with_history("how does it differ from Meyerhof?", history)
    assert out == expanded


async def test_overlong_llm_output_truncated_with_history(monkeypatch):
    long_text = " ".join(f"word{i}" for i in range(40))
    _install_fake_groq(monkeypatch, return_text=long_text)
    history = [{"role": "user", "content": "Discuss slope stability."}]
    out = await rewrite_query_with_history("go on", history)
    assert len(out.split()) == 30


async def test_llm_failure_falls_back_to_raw_query(monkeypatch):
    _install_fake_groq(monkeypatch, raise_exc=RuntimeError("groq down"))
    query = "孔隙水压力分析"   # non-English -> takes the LLM path, which then fails
    out = await rewrite_query_with_history(query, history=None)
    assert out == query   # fallback to raw query so retrieval still happens
