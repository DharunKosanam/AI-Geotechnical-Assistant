"""
Observability regressions from the 2026-08-26 incidents.

1. Thread title generation (POST /api/assistants/threads/{id}/title) must
   never fail silently: success, failure (with the exception type + message)
   and the empty-text skip each print a line naming the thread.
2. The short-answer guard in llm_service must print a ``[GUARD] diag:`` line
   (done_reason, token counts, wall time, attempt, mode, streaming, in-flight
   count) on a short/empty raw answer -- and NOT on a healthy one.

Pure unit tests: the LLM client is faked, nothing touches Ollama or Mongo.
"""
import asyncio
from types import SimpleNamespace

import pytest

from models import TitleGenerationRequest
from app.routers import threads as threads_router
from app.services import llm_service

pytestmark = pytest.mark.unit

USER = SimpleNamespace(id="user-title-test")


# --- title endpoint ------------------------------------------------------------

def _title(monkeypatch, llm, message):
    monkeypatch.setattr(threads_router, "get_llm", lambda: llm)
    return asyncio.run(
        threads_router.generate_thread_title(
            "thread_title_x", TitleGenerationRequest(message=message), current_user=USER
        )
    )


def _lines(capsys, needle):
    return [l for l in capsys.readouterr().out.splitlines() if needle in l]


def test_title_failure_logs_exception_type_and_message(monkeypatch, capsys):
    class BoomLLM:
        async def acomplete(self, prompt):
            raise ConnectionError("ollama unreachable at :11434")

    result = _title(monkeypatch, BoomLLM(), "bearing capacity of soft clay")

    # Contract unchanged: the caller still gets a usable (timestamp) title.
    assert result["title"]
    errors = _lines(capsys, "[ERROR] Error generating title for thread thread_title_x")
    assert len(errors) == 1, errors
    assert "ConnectionError" in errors[0]
    assert "ollama unreachable at :11434" in errors[0]
    assert "title" in errors[0].lower()  # what `journalctl | grep -i title` sees


def test_title_success_logs_arrival_and_result(monkeypatch, capsys):
    class OkLLM:
        async def acomplete(self, prompt):
            return SimpleNamespace(text='"Clay Bearing Capacity Estimate."\n')

    result = _title(monkeypatch, OkLLM(), "bearing capacity of soft clay")

    assert result == {"title": "Clay Bearing Capacity Estimate"}
    out = capsys.readouterr().out
    assert "[TITLE] Generating title for thread thread_title_x" in out
    assert "[OK] Generated title for thread thread_title_x" in out
    assert "[ERROR]" not in out


def test_title_skip_on_empty_text_logs(monkeypatch, capsys):
    class NeverCalled:
        async def acomplete(self, prompt):  # pragma: no cover
            raise AssertionError("LLM must not be called for empty text")

    result = _title(monkeypatch, NeverCalled(), "")

    assert result["title"]
    skipped = _lines(capsys, "[TITLE] Skipped for thread thread_title_x")
    assert len(skipped) == 1, skipped


def test_title_unusable_llm_output_logs_and_falls_back(monkeypatch, capsys):
    class JunkLLM:
        async def acomplete(self, prompt):
            return SimpleNamespace(text="<think>only reasoning</think>")

    result = _title(monkeypatch, JunkLLM(), "settlement of a raft foundation on sand")

    assert result == {"title": "settlement of a raft foundation"}
    out = capsys.readouterr().out
    assert "[TITLE] LLM output unusable after cleaning for thread thread_title_x" in out
    assert "[OK] Generated title for thread thread_title_x" in out


# --- short-answer guard diagnostics ---------------------------------------------

def _empty_stream_client(final_chunk, retry_response):
    class FakeStream:
        def __init__(self):
            self.chunks = [
                {"message": {"content": "", "thinking": "hmm"}, "done": False},
                final_chunk,
            ]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.chunks:
                raise StopAsyncIteration
            return self.chunks.pop(0)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, **kw):
            if kw.get("stream"):
                return FakeStream()
            return retry_response

        async def close(self):
            pass

    return FakeClient


def test_streaming_guard_logs_diagnostics_for_initial_and_retry(monkeypatch, capsys):
    final_chunk = {
        "message": {"content": ""}, "done": True, "done_reason": "stop",
        "prompt_eval_count": 6400, "eval_count": 1, "total_duration": 2_500_000_000,
        "load_duration": 1_000_000,
    }
    retry_response = {
        "message": {"content": "", "thinking": "x" * 50}, "done": True,
        "done_reason": "length", "prompt_eval_count": 12, "eval_count": 2048,
    }
    monkeypatch.setattr(llm_service.ollama, "AsyncClient", _empty_stream_client(final_chunk, retry_response))
    emitted = []

    async def emit(text):
        emitted.append(text)

    final = asyncio.run(llm_service._ollama_stream_and_clean("p" * 1000, emit, mode="KB_QUERY"))

    assert final == "I couldn't generate a complete answer — please try again."
    assert "".join(emitted) == final
    out = capsys.readouterr().out
    diag = [l for l in out.splitlines() if "[GUARD] diag:" in l]
    assert len(diag) == 2, out
    initial, retry = diag
    for needle in ("attempt=initial", "mode=KB_QUERY", "streaming=True", "done_reason='stop'",
                   "prompt_eval_count=6400", "eval_count=1", "prompt_chars=1000",
                   "thinking_chars=3", "total_duration_ms=2500", "load_duration_ms=1",
                   "in_flight=1", "wall="):
        assert needle in initial, (needle, initial)
    for needle in ("attempt=retry", "mode=KB_QUERY", "streaming=False", "done_reason='length'",
                   "eval_count=2048", "thinking_chars=50", "in_flight=1"):
        assert needle in retry, (needle, retry)
    # The counter is balanced afterwards (both attempts decremented).
    assert llm_service._GENERATIONS_IN_FLIGHT == 0


def test_streaming_guard_is_silent_on_healthy_answer(monkeypatch, capsys):
    body = "A healthy answer long enough to clear the short-answer guard easily."
    final_chunk = {"message": {"content": body}, "done": True, "done_reason": "stop"}
    monkeypatch.setattr(llm_service.ollama, "AsyncClient", _empty_stream_client(final_chunk, None))

    async def emit(text):
        pass

    final = asyncio.run(llm_service._ollama_stream_and_clean("prompt", emit, mode="GENERAL"))

    assert final == body
    assert "[GUARD]" not in capsys.readouterr().out
    assert llm_service._GENERATIONS_IN_FLIGHT == 0


def test_non_streaming_guard_logs_diagnostics(monkeypatch, capsys):
    responses = [
        {"message": {"content": ""}, "done": True, "done_reason": "stop",
         "prompt_eval_count": 300, "eval_count": 0},
        {"message": {"content": " "}, "done": True, "done_reason": "stop",
         "prompt_eval_count": 0, "eval_count": 0},
    ]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, **kw):
            assert not kw.get("stream")
            return responses.pop(0)

        async def close(self):
            pass

    monkeypatch.setattr(llm_service.ollama, "AsyncClient", FakeClient)
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm_service, "get_llm", lambda: None)

    final = asyncio.run(llm_service.generate_answer_with_groq("q?", "ctx", None, mode="KB_QUERY"))

    assert final == "I couldn't generate a complete answer — please try again."
    diag = [l for l in capsys.readouterr().out.splitlines() if "[GUARD] diag:" in l]
    assert len(diag) == 2, diag
    assert "attempt=initial" in diag[0] and "streaming=False" in diag[0] and "mode=KB_QUERY" in diag[0]
    assert "prompt_eval_count=300" in diag[0] and "in_flight=1" in diag[0]
    assert "attempt=retry" in diag[1] and "streaming=False" in diag[1]
    assert llm_service._GENERATIONS_IN_FLIGHT == 0


def test_guard_counter_is_decremented_when_the_call_raises(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, **kw):
            raise RuntimeError("boom")

        async def close(self):
            pass

    monkeypatch.setattr(llm_service.ollama, "AsyncClient", FakeClient)

    async def emit(text):
        pass

    with pytest.raises(RuntimeError):
        asyncio.run(llm_service._ollama_stream_and_clean("prompt", emit))
    assert llm_service._GENERATIONS_IN_FLIGHT == 0
