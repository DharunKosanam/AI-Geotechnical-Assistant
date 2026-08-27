"""Regression tests for the mid-answer truncation bug in the LLM output cleaner.

``_clean_llm_answer`` used to strip every ``<...>`` span, so a bare comparison
``<`` in the answer deleted everything up to the next ``>`` anywhere later in
the text. ``_stable_raw_prefix`` also held the stream at every ``<`` until some
``>`` arrived. Both must now only care about ``<think>`` tags, and the streaming
invariant -- concatenation of everything emitted equals the final answer --
must hold for answers full of ``<`` and ``>``.
"""
import asyncio

import pytest

from app.services import llm_service
from app.services.llm_service import _clean_llm_answer, _stable_raw_prefix


@pytest.fixture(autouse=True)
def _ollama_provider(monkeypatch):
    # The scrub only runs for providers/models that emit think tags; Ollama does.
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "ollama")


# --- _clean_llm_answer ------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "no significant fines (e.g., $",
        "value $D_{50} = 0.4$ mm here",
        "phi > 35 degrees",
        "a < b and c > d",
        "fines (e.g., $D_{50} < 0.5$ mm). **Example**: A dense sand with phi > 38 deg.",
        "x < 5 and later the tag <b>bold</b> ok",
        "strain rate <1%/min and Cu >= 4 -> well graded",
    ],
)
def test_clean_preserves_angle_brackets_and_latex(raw):
    assert _clean_llm_answer(raw, allow_raw_fallback=False) == raw


def test_clean_still_removes_paired_think_block():
    raw = "<think>reasoning with a < b inside</think>\nphi > 38 deg."
    assert _clean_llm_answer(raw, allow_raw_fallback=False) == "phi > 38 deg."


def test_clean_removes_stray_unpaired_think_tags():
    assert _clean_llm_answer("</think>\nanswer a < b", allow_raw_fallback=False) == "answer a < b"
    assert _clean_llm_answer("answer <THINK>", allow_raw_fallback=False) == "answer"


# --- _stable_raw_prefix -----------------------------------------------------

def test_prefix_does_not_hold_at_comparison_operators():
    # A "<" followed by anything that cannot grow into "<think>" is settled.
    for raw in ["a < b and c", "phi >", "fines (e.g., $D_{50} < 0", "x < 5", "1 < Cc"]:
        assert _stable_raw_prefix(raw) == raw.rstrip()
    # A lone trailing "<" is held for exactly one token (it could still become
    # "<think>"); the next character resolves it.
    assert _stable_raw_prefix("fines (e.g., $D_{50} <") == "fines (e.g., $D_{50}"
    assert _stable_raw_prefix("fines (e.g., $D_{50} < ") == "fines (e.g., $D_{50} <"


def test_prefix_holds_partial_think_tags_only():
    assert _stable_raw_prefix("answer <") == "answer"          # could become <think>
    assert _stable_raw_prefix("answer <thin") == "answer"
    assert _stable_raw_prefix("answer </thi") == "answer"
    assert _stable_raw_prefix("answer <bo") == "answer <bo"    # cannot become a think tag
    assert _stable_raw_prefix("answer <b>bold") == "answer <b>bold"


def test_prefix_holds_unterminated_think_block():
    assert _stable_raw_prefix("answer <think>secret a < b") == "answer"


# --- streaming invariant: emitted == final ----------------------------------

def _stream_pieces(text: str, size: int = 3):
    return [text[i:i + size] for i in range(0, len(text), size)]


def _simulate_stream(raw: str) -> tuple[str, str]:
    """Mirror _ollama_stream_and_clean's prefix-buffer loop exactly."""
    raw_parts, emitted = [], ""
    for piece in _stream_pieces(raw):
        raw_parts.append(piece)
        candidate = _clean_llm_answer(_stable_raw_prefix("".join(raw_parts)), allow_raw_fallback=False)
        if len(candidate) < llm_service.SHORT_ANSWER_THRESHOLD:
            continue
        if not candidate.startswith(emitted):
            raise AssertionError(f"non-monotonic clean: {emitted!r} -> {candidate!r}")
        emitted = candidate
    final = _clean_llm_answer("".join(raw_parts))
    return emitted, final


@pytest.mark.parametrize(
    "raw",
    [
        "Dense sand has no significant fines (e.g., $D_{50} < 0.5$ mm). **Example**: A dense sand with phi > 38 deg and D_r > 75%.",
        "Cu = D60/D10 > 6 and 1 < Cc < 3 means well graded; otherwise poorly graded. Check <b>both</b> criteria.",
        "<think>plan: mention a < b</think>The friction angle of dense sand is typically > 35 degrees, often 38-45.",
    ],
)
def test_stream_emits_exactly_the_final_answer(raw):
    emitted, final = _simulate_stream(raw)
    # Nothing may be held back at the end, and nothing dropped mid-stream.
    assert emitted == final
    assert "<think>" not in final and "</think>" not in final
    assert "> " in final or " >" in final  # the comparison text survived


def test_real_stream_loop_invariant(monkeypatch):
    """Drive the actual _ollama_stream_and_clean with a fake Ollama client."""
    raw = "Dense sand: fines (e.g., $D_{50} < 0.5$ mm). **Example**: phi > 38 deg, D_r > 75%, and 1 < Cc < 3."

    class FakeStream:
        def __init__(self):
            self.pieces = _stream_pieces(raw, 4)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.pieces:
                raise StopAsyncIteration
            return {"message": {"content": self.pieces.pop(0)}}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, **kw):
            assert kw.get("stream") is True
            return FakeStream()

        async def close(self):
            pass

    monkeypatch.setattr(llm_service.ollama, "AsyncClient", FakeClient)
    monkeypatch.setattr(llm_service, "LLM_PROVIDER", "ollama")

    emitted = []

    async def emit(text):
        emitted.append(text)

    final = asyncio.run(llm_service._ollama_stream_and_clean("prompt", emit))
    assert "".join(emitted) == final == raw
