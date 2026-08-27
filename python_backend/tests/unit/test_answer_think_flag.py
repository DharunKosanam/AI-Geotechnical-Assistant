"""
OLLAMA_THINK_ANSWERS (2026-08-26): thinking on the ANSWER path is env-controlled
and OFF by default. num_predict caps thinking and answer together, and with
think=True gemma4 spends the whole budget reasoning on hard questions
(done_reason='length', content='') -- the "empty answer" incident. The flag is
read at call time, so these tests toggle it on the config module directly.
Classifiers and the title call are not governed by it.
"""
import asyncio
import os

import pytest

from app.core import config
from app.services import llm_service

pytestmark = pytest.mark.unit

BODY = "an answer body that is long enough to clear the short-answer guard easily."


class _Rec:
    calls = []

    def __init__(self, *a, **k):
        pass

    async def chat(self, **kw):
        _Rec.calls.append(kw)
        if kw.get("stream"):
            async def gen():
                yield {"message": {"content": BODY}, "done": True, "done_reason": "stop"}
            return gen()
        return {"message": {"content": BODY}, "done": True, "done_reason": "stop"}

    async def close(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    _Rec.calls = []
    monkeypatch.setattr(llm_service.ollama, "AsyncClient", _Rec)
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_service, "get_llm", lambda: None)
    return _Rec


async def _noop(text):
    pass


def _answer():
    return asyncio.run(llm_service.generate_answer_with_groq("q?", "ctx", None, mode="KB_QUERY"))


def _stream():
    return asyncio.run(llm_service._ollama_stream_and_clean("prompt", _noop, mode="KB_QUERY"))


@pytest.mark.skipif(os.getenv("OLLAMA_THINK_ANSWERS") is not None,
                    reason="env overrides the default in this process")
def test_flag_defaults_to_off():
    assert config.OLLAMA_THINK_ANSWERS is False


def test_default_off_sends_think_false_on_both_paths(fake, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_THINK_ANSWERS", False)
    assert _answer() == BODY
    assert _stream() == BODY
    assert [c["think"] for c in fake.calls] == [False, False]


def test_flag_on_restores_think_true_on_both_paths(fake, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_THINK_ANSWERS", True)
    assert _answer() == BODY
    assert _stream() == BODY
    assert [c["think"] for c in fake.calls] == [True, True]


def test_flag_is_read_at_call_time_not_import_time(fake, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_THINK_ANSWERS", False)
    _answer()
    monkeypatch.setattr(config, "OLLAMA_THINK_ANSWERS", True)
    _answer()
    monkeypatch.setattr(config, "OLLAMA_THINK_ANSWERS", False)
    _stream()
    assert [c["think"] for c in fake.calls] == [False, True, False]


def test_guard_retry_follows_the_flag_too(monkeypatch):
    responses = [
        {"message": {"content": ""}, "done": True, "done_reason": "length", "eval_count": 2048},
        {"message": {"content": ""}, "done": True, "done_reason": "length", "eval_count": 2048},
    ]

    class Empty(_Rec):
        async def chat(self, **kw):
            _Rec.calls.append(kw)
            return responses.pop(0)

    _Rec.calls = []
    monkeypatch.setattr(llm_service.ollama, "AsyncClient", Empty)
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_service, "get_llm", lambda: None)
    monkeypatch.setattr(config, "OLLAMA_THINK_ANSWERS", True)
    assert _answer().startswith("I couldn't generate a complete answer")
    assert [c["think"] for c in _Rec.calls] == [True, True]


def test_title_call_stays_thinking_off_regardless_of_flag(monkeypatch):
    from app.routers import threads
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OLLAMA_THINK_ANSWERS", True)
    llm = threads._title_llm()
    assert llm.thinking is False
    assert llm._model_kwargs["num_ctx"] == config.OLLAMA_NUM_CTX
